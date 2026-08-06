"""Tests for app/voice.py, protocol v2 (device STT/TTS).

v1 tests drove _pump_events with fake ADK live events; v2 turns are client-
driven TEXT frames, so these tests drive _receive_once / _handle_text_frame
directly (the same style the v1 tests used for the event pump) and use a
run_async fake instead of a run_live one.
"""
import asyncio
import json
import threading

import pytest
from fastapi import WebSocketDisconnect
from google.genai import types

import app.voice as voice_mod
from app import antispoof, config, speaker as speaker_mod, trust, voice_protocol as vp, voice_trust
from app.voice import APP_NAME, VoiceBridge, _handshake

USER = "kadir@example.com"


@pytest.fixture(autouse=True)
def _default_fake_antispoof(monkeypatch):
    monkeypatch.setattr(antispoof, "_score_fn", lambda pcm: (True, 0.0))


def _trust_key(user_id=USER):
    return voice_trust.key_for(APP_NAME, user_id, f"voice-{user_id}")


def _armed(bridge, user_id=USER):
    """Give a bridge the session key/id run() would have established, so unit
    tests that drive the frame handlers directly still exercise the real
    publish path instead of _publish_trust's no-op guard."""
    bridge._trust_key = _trust_key(user_id)
    bridge._user_id = user_id
    bridge._session_id = f"voice-{user_id}"
    return bridge


def _user_text(text, final=True):
    return json.dumps({"type": "user_text", "text": text, "utterance_final": final})


async def _drive_turns(bridge):
    """Await every turn task a user_text frame spawned (they never raise --
    _run_turn swallows and logs -- so gather is safe)."""
    if bridge._turn_tasks:
        await asyncio.gather(*bridge._turn_tasks)


def _sent_json(ws):
    return [json.loads(t) for kind, t in ws.sent if kind == "text"]


class FakeWS:
    """Minimal duck-type of fastapi WebSocket used by VoiceBridge.run."""

    def __init__(self, incoming):
        self.incoming = list(incoming)  # list of dicts like starlette receive()
        self.sent = []

    async def receive(self):
        if not self.incoming:
            await asyncio.sleep(3600)
        return self.incoming.pop(0)

    async def send_bytes(self, b):
        self.sent.append(("bytes", b))

    async def send_text(self, t):
        self.sent.append(("text", t))


class YieldingFakeWS(FakeWS):
    """Like FakeWS, but yields to the event loop on every receive(), the way a
    real ASGI socket read always would -- this lets concurrently running turn
    tasks actually get scheduled before the receive loop exits."""

    async def receive(self):
        await asyncio.sleep(0)
        return await super().receive()

    async def close(self, code=1000):
        self.sent.append(("close", code))


class ScriptedGatedWS(YieldingFakeWS):
    """Serves the scripted messages, then blocks on `gate` before reporting a
    disconnect -- models a real client that keeps the socket open while the
    turn is being served, and makes run()-level tests deterministic: the test
    sets the gate only once the behaviour under test has played out."""

    def __init__(self, incoming, gate):
        super().__init__(incoming)
        self.gate = gate

    async def receive(self):
        if self.incoming:
            return await super().receive()
        await self.gate.wait()
        return {"type": "websocket.disconnect"}


class _FinalResponse:
    """Mirrors the ADK Event surface _serve_turn reads: is_final_response()
    plus content.parts[0].text (main.run_turn collects replies the same way)."""

    def __init__(self, text):
        self.content = types.Content(role="model", parts=[types.Part(text=text)])

    def is_final_response(self):
        return True


class FakeTextRunner:
    """Stands in for ADK's Runner.run_async: records the call, yields one
    final-response event carrying the canned reply (reply=None yields nothing
    -- the empty-reply path)."""

    def __init__(self, reply="selam"):
        self.reply = reply
        self.calls = []

    def run_async(self, *, user_id, session_id, new_message):
        self.calls.append({
            "user_id": user_id, "session_id": session_id, "new_message": new_message,
        })
        reply = self.reply

        async def events():
            if reply is not None:
                yield _FinalResponse(reply)

        return events()


class _NoTurnsRunner:
    """A runner whose run_async is never expected to be called (run-level
    tests that drive no user_text); yields nothing if it is."""

    def run_async(self, **kwargs):
        async def events():
            return
            yield  # pragma: no cover

        return events()


class _StatelessSessions:
    """Session service whose sessions are bare objects with no `.state` -- the
    bridge must never depend on anything but their existence."""

    async def get_session(self, **k):
        return object()

    async def create_session(self, **k):
        return object()


class FakeMemory:
    """Fake Memory: records snapshot_session calls without touching Firestore."""

    def __init__(self):
        self.calls = []

    def snapshot_session(self, session_id, user_id, summary):
        self.calls.append((session_id, user_id, summary))


# --- the v2 frame flow: user_text -> verify -> turn -> jarvis_text ----------


@pytest.mark.asyncio
async def test_user_text_final_runs_a_turn_and_answers_jarvis_text():
    """THE v2 happy path, pinned end to end: the final STT text runs exactly
    one run_async turn on the voice session, the reply goes out as jarvis_text
    (device TTS speaks it), both sides land in the transcript events AND in
    self.transcript (snapshot source), the turn closes with turn_complete --
    and NOT ONE binary frame leaves the server."""
    runner = FakeTextRunner("selam")
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([])
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["user_id"] == USER
    assert call["session_id"] == f"voice-{USER}"
    assert call["new_message"].role == "user"
    assert call["new_message"].parts[0].text == "merhaba"

    # No transcript("user") echo: the v2 client renders its own STT final
    # locally; a server echo would duplicate the row.
    assert _sent_json(ws) == [
        {"type": "jarvis_text", "text": "selam"},
        {"type": "transcript", "role": "jarvis", "text": "selam"},
        {"type": "turn_complete"},
    ]
    assert bridge.transcript == [
        {"role": "user", "text": "merhaba"},
        {"role": "jarvis", "text": "selam"},
    ]
    assert all(kind != "bytes" for kind, _ in ws.sent), "v2 must never send binary audio"


@pytest.mark.asyncio
async def test_a_non_final_user_text_is_ignored_without_an_error_frame(caplog):
    """A partial STT result is not an utterance boundary: no turn, no
    transcript, and NO evt_error -- error frames are user-visible alarm UI and
    routine partials must not trip it. Logged with the DATA instead."""
    runner = FakeTextRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([])
    with caplog.at_level("INFO"):
        await bridge._handle_text_frame(ws, _user_text("merhaba nas", final=False))
    assert runner.calls == []
    assert ws.sent == []
    assert bridge.transcript == []
    assert "not an utterance boundary" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_final_user_text_is_ignored(caplog):
    """An empty final is a false VAD trigger: there is nothing to answer."""
    runner = FakeTextRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([])
    with caplog.at_level("INFO"):
        await bridge._handle_text_frame(ws, _user_text("   "))
    assert runner.calls == []
    assert ws.sent == []
    assert "not an utterance boundary" in caplog.text


@pytest.mark.asyncio
async def test_unknown_and_malformed_frames_are_ignored(caplog):
    """A stray frame must never break the audio stream (same failure
    philosophy as _verify_utterance)."""
    runner = FakeTextRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([])
    with caplog.at_level("INFO"):
        await bridge._handle_text_frame(ws, json.dumps({"type": "partial", "text": "x"}))
    with caplog.at_level("WARNING"):
        await bridge._handle_text_frame(ws, "definitely not json")
    assert runner.calls == []
    assert ws.sent == []
    assert "unknown client frame type" in caplog.text
    assert "non-JSON text frame" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_model_reply_sends_no_jarvis_text_but_closes_the_turn(caplog):
    """An empty final response is a real model outcome (e.g. a turn that only
    called tools). An empty jarvis_text would make the device TTS say nothing
    after a thinking pause -- log it as DATA instead, and still close the turn
    so the client's 'thinking' state terminates."""
    runner = FakeTextRunner(reply=None)
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([])
    with caplog.at_level("INFO"):
        await bridge._handle_text_frame(ws, _user_text("merhaba"))
        await _drive_turns(bridge)

    sent = _sent_json(ws)
    assert {"type": "turn_complete"} in sent, "the turn must still close"
    assert all(e["type"] != "jarvis_text" for e in sent)
    assert bridge.transcript == [{"role": "user", "text": "merhaba"}]
    assert "empty model reply" in caplog.text


@pytest.mark.asyncio
async def test_a_failing_turn_answers_error_plus_turn_complete_and_stays_alive():
    """A runner failure must not kill the connection: the client gets
    evt_error + turn_complete (its thinking state terminates), the failure is
    retrieved inside the task (no asyncio 'exception was never retrieved'),
    and the NEXT utterance is served normally."""

    class FailOnceRunner:
        def __init__(self):
            self.calls = 0

        def run_async(self, **kwargs):
            self.calls += 1
            first = self.calls == 1

            async def events():
                if first:
                    raise RuntimeError("model stream died")
                yield _FinalResponse("selam")

            return events()

    runner = FailOnceRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([])

    loop = asyncio.get_running_loop()
    unhandled = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))
    try:
        await bridge._handle_text_frame(ws, _user_text("birinci"))
        await _drive_turns(bridge)
        await bridge._handle_text_frame(ws, _user_text("ikinci"))
        await _drive_turns(bridge)
    finally:
        loop.set_exception_handler(previous_handler)

    assert unhandled == [], "a turn task leaked an unretrieved exception"
    sent = _sent_json(ws)
    assert {"type": "error", "message": "İstek işlenemedi, tekrar dene"} in sent
    assert sent[-1] == {"type": "turn_complete"}
    assert {"type": "jarvis_text", "text": "selam"} in sent, (
        "the connection died with the first turn -- the second utterance was never served")


# --- turn serialization: one run_async at a time, queued FIFO ----------------


@pytest.mark.asyncio
async def test_concurrent_user_texts_are_serialized_not_dropped():
    """With STT on the device, a second final can legitimately arrive while
    the previous turn is still generating (a quick follow-up, a barge-in
    utterance). Dropping it would silently lose user speech; running both
    concurrently would interleave two model turns on one session. The bridge
    QUEUES them on a lock: both turn, one at a time, in arrival order
    (asyncio.Lock wakes waiters FIFO)."""
    active = 0
    max_active = 0
    calls = []

    class ConcurrencyRecordingRunner:
        def run_async(self, *, user_id, session_id, new_message):
            text = new_message.parts[0].text
            calls.append(text)

            async def events():
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                try:
                    for _ in range(5):
                        await asyncio.sleep(0)   # widen any overlap window
                    yield _FinalResponse(f"cevap:{text}")
                finally:
                    active -= 1

            return events()

    bridge = _armed(VoiceBridge(runner=ConcurrencyRecordingRunner(), session_service=None))
    ws = FakeWS([])
    await bridge._handle_text_frame(ws, _user_text("birinci"))
    await bridge._handle_text_frame(ws, _user_text("ikinci"))
    await _drive_turns(bridge)

    assert max_active == 1, "two turns ran concurrently on one session"
    assert calls == ["birinci", "ikinci"], (
        "a queued utterance was dropped or reordered -- user speech lost")
    replies = [e["text"] for e in _sent_json(ws) if e["type"] == "jarvis_text"]
    assert replies == ["cevap:birinci", "cevap:ikinci"]


# --- speech_start: the onset trim --------------------------------------------


@pytest.mark.asyncio
async def test_the_first_speech_start_trims_the_buffer_to_the_onset():
    """At a speech onset the buffer is [audio collected since the last
    boundary] + [this utterance so far] -- the mic never stopped. Keeping all
    of it means the utterance is scored with up to a full window of
    inter-turn room noise in front of it, and speaker.embed averages the
    whole PCM into one embedding. Keep only the onset window."""
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=FakeSpeaker((True, 0.9))))
    noise = b"\x00" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 3)
    onset = b"\x11" * config.SPEAKER_BARGE_IN_ONSET_BYTES
    bridge._utterance = bytearray(noise + onset)

    await bridge._handle_text_frame(FakeWS([]), json.dumps({"type": "speech_start"}))

    kept = bytes(bridge._utterance)
    assert kept == onset, (
        f"kept {len(kept)} of {len(noise + onset)} buffered bytes -- inter-turn "
        "noise is still in front of the utterance")


@pytest.mark.asyncio
async def test_a_repeat_speech_start_does_not_trim_the_live_utterance():
    """The onset trim answers "where did the speech start?" -- which is only
    an open question for the FIRST speech_start of an utterance. On a repeat
    the audio in front of the window is the user's own speech, already
    accumulating; trimming again would throw it away and score whatever 0.5 s
    happened to be at the tail."""
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=FakeSpeaker((True, 0.9))))
    noise = b"\x00" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 3)
    onset = b"\x11" * config.SPEAKER_BARGE_IN_ONSET_BYTES
    kept_speaking = b"\x22" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 2)
    bridge._utterance = bytearray(noise + onset)

    ws = FakeWS([])
    await bridge._handle_text_frame(ws, json.dumps({"type": "speech_start"}))
    bridge._utterance.extend(kept_speaking)          # the user keeps talking
    await bridge._handle_text_frame(ws, json.dumps({"type": "speech_start"}))

    assert bytes(bridge._utterance) == onset + kept_speaking, (
        f"the repeat speech_start cut the live utterance down to "
        f"{len(bridge._utterance)} bytes; {len(onset + kept_speaking)} had been spoken")


@pytest.mark.asyncio
async def test_the_onset_latch_resets_at_the_utterance_final():
    """The latch is per-UTTERANCE: after the final, the NEXT utterance's first
    speech_start must trim again -- otherwise every utterance after the first
    is scored with inter-turn noise in front of it."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=speaker))
    ws = FakeWS([])
    await bridge._handle_text_frame(ws, json.dumps({"type": "speech_start"}))
    await bridge._handle_text_frame(ws, _user_text("birinci"))
    await _drive_turns(bridge)
    assert bridge._speech_open is False

    noise = b"\x00" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 2)
    onset = b"\x33" * config.SPEAKER_BARGE_IN_ONSET_BYTES
    bridge._utterance = bytearray(noise + onset)
    await bridge._handle_text_frame(ws, json.dumps({"type": "speech_start"}))
    assert bytes(bridge._utterance) == onset, (
        "the latch leaked into the next utterance -- its onset was never trimmed")


def test_the_onset_window_is_a_real_positive_slice_of_audio():
    """voice.py trims with `del buf[:-onset]`, and `del buf[:-0]` is
    `del buf[:0]` -- a NO-OP. The constant must therefore always be positive
    (and reachable, i.e. within the mic window), and a usable slice of audio,
    not a token byte."""
    assert 0 < config.SPEAKER_BARGE_IN_ONSET_BYTES <= config.SPEAKER_UTTERANCE_MAX_BYTES
    assert config.SPEAKER_BARGE_IN_ONSET_BYTES >= int(0.25 * vp.AUDIO_IN_RATE * 2), (
        "the onset window degraded to practically no audio at all")


# --- the utterance buffer ----------------------------------------------------


@pytest.mark.asyncio
async def test_receive_buffers_utterance_only_when_speaker_service_set():
    """Exercises the buffering guard through the REAL entry point
    (_receive_once): no speaker service means identity is off, and the PCM is
    not kept at all."""
    with_speaker = VoiceBridge(runner=None, session_service=None,
                               speaker_service=FakeSpeaker((True, 0.9)))
    ws = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01\x02\x03"}])
    await with_speaker._receive_once(ws)
    assert bytes(with_speaker._utterance) == b"\x00\x01\x02\x03"

    without_speaker = VoiceBridge(runner=None, session_service=None)  # speaker_service=None
    ws2 = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01\x02\x03"}])
    await without_speaker._receive_once(ws2)
    assert bytes(without_speaker._utterance) == b""


@pytest.mark.asyncio
async def test_utterance_buffer_is_capped_to_the_configured_window(monkeypatch):
    """The buffer is only drained at an utterance boundary, and a user_text
    final is not guaranteed to arrive (the device VAD can sit through long
    silence). Unbounded it grows at 32 KB/s in a torch-carrying process. It
    must keep the most RECENT window, not the oldest -- the newest audio is
    the utterance the next verification is about."""
    monkeypatch.setattr(config, "SPEAKER_UTTERANCE_MAX_BYTES", 8)
    bridge = VoiceBridge(runner=None, session_service=None,
                         speaker_service=FakeSpeaker((True, 0.9)))
    for chunk in (b"aaaa", b"bbbb", b"cccc", b"dddd"):
        ws = FakeWS([{"type": "websocket.receive", "bytes": chunk}])
        await bridge._receive_once(ws)

    assert bytes(bridge._utterance) == b"ccccdddd"        # newest 8 bytes only


def test_configured_window_is_ten_seconds_of_contract_audio():
    """Pins the unit: SPEAKER_UTTERANCE_MAX_BYTES is BYTES derived from seconds
    x the protocol's input rate x 2 bytes/sample, so the seconds knob and the
    byte cap can never drift apart."""
    assert config.SPEAKER_UTTERANCE_MAX_BYTES == int(
        config.SPEAKER_UTTERANCE_SECONDS * voice_mod.vp.AUDIO_IN_RATE * 2)
    assert config.SPEAKER_UTTERANCE_MAX_BYTES == 320000    # 10 s @ 16 kHz PCM16


@pytest.mark.asyncio
async def test_disconnect_stops_the_receive_loop():
    ws = FakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(runner=None, session_service=None)
    assert await bridge._receive_once(ws) is False


# --- speaker verification at the v2 boundary ---------------------------------


class FakeSpeaker:
    """Fake SpeakerService: canned IdentifyOutcome, records identify AND
    record_history calls."""

    def __init__(self, result):  # (verified, score)
        verified, score = result
        self.result = speaker_mod.IdentifyOutcome(
            verified=verified, score=score, vec=[0.5, 0.5],
            adapted_sample_id=None)
        self.calls = []
        self.allow_adapt_calls = []
        self.history = []

    def identify(self, user_id, pcm, device_hint, auth_is_kadir, allow_adapt=True, cm_ok=None):
        self.calls.append((user_id, pcm, device_hint, auth_is_kadir))
        self.allow_adapt_calls.append(allow_adapt)
        return self.result

    def record_history(self, user_id, **entry):
        self.history.append((user_id, entry))
        return "h1"


@pytest.mark.asyncio
async def test_final_verifies_the_buffered_pcm_and_clears_the_buffer():
    """The v2 utterance boundary (user_text final) drives the SAME
    verification the v1 finished-transcription did: identify on exactly the
    buffered PCM, trust published, speaker event sent, buffer drained."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=speaker,
                                device_hint="headset", presence="locked"))
    ws = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01\x02\x03"}])
    await bridge._receive_once(ws)                       # the mic, mid-utterance
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)

    assert speaker.calls and speaker.calls[0][1] == b"\x00\x01\x02\x03"
    assert speaker.calls[0][2] == "headset"              # identify called w/ device
    assert bytes(bridge._utterance) == b""
    # locked + verified -> MEDIUM, carried to the policy layer with the WHY
    # fields the audit trail needs (spec §7).
    signals = voice_trust.peek(_trust_key())
    assert signals is not None
    assert (signals.trust_level, signals.voice_score, signals.presence, signals.device_hint) == (
        trust.MEDIUM, 0.9, "locked", "headset")
    assert {"type": "speaker", "role": "user", "verified": True, "score": 0.9, "cm_ok": True} in _sent_json(ws)


@pytest.mark.asyncio
async def test_trust_is_published_before_the_turn_runs():
    """ORDER IS LOAD-BEARING: the policy callback reads the trust level during
    THIS turn's tool calls, so the publish must land before run_async starts.
    Proven by sampling the registry from INSIDE the runner."""
    seen = {}

    class InspectingRunner:
        def run_async(self, **kwargs):
            seen["signals"] = voice_trust.peek(_trust_key())

            async def events():
                yield _FinalResponse("selam")

            return events()

    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=InspectingRunner(), session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._handle_text_frame(FakeWS([]), _user_text("merhaba"))
    await _drive_turns(bridge)

    assert seen["signals"] is not None, "the turn ran before any trust was published"
    assert seen["signals"].trust_level == trust.MEDIUM, (
        "the turn ran under the PREVIOUS level -- the publish must precede run_async")
    assert seen["signals"].voice_score == 0.9


@pytest.mark.asyncio
async def test_speaker_event_precedes_the_model_reply():
    """Client-visible ordering: speaker -> jarvis_text -> transcript(jarvis)
    -> turn_complete. (No transcript("user") echo in v2 -- the client renders
    its own STT final locally.)"""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner("selam"), session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    ws = FakeWS([])
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)

    assert [e["type"] for e in _sent_json(ws)] == [
        "speaker", "jarvis_text", "transcript", "turn_complete",
    ]


@pytest.mark.asyncio
async def test_a_short_utterance_is_verified_with_no_length_floor():
    """The v2 boundary passes min_bytes=0: the device's STT final is itself
    the positive signal that a whole utterance preceded it. A floor here
    would silently stop verifying short commands ("evet", "kapat") -- exactly
    what the old finished-transcription path avoided too."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x02\x03")           # ~0.1 ms of audio
    await bridge._handle_text_frame(FakeWS([]), _user_text("evet"))
    await _drive_turns(bridge)
    assert len(speaker.calls) == 1


@pytest.mark.asyncio
async def test_a_final_with_an_empty_buffer_does_not_verify():
    """No PCM arrived (mic permission off, pure barge-in before any audio):
    verification is a no-op -- no identify call, no speaker event -- but the
    text turn still runs."""
    speaker = FakeSpeaker((True, 0.9))
    runner = FakeTextRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None,
                                speaker_service=speaker, presence="locked"))
    ws = FakeWS([])
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)
    assert speaker.calls == []
    assert all(e["type"] != "speaker" for e in _sent_json(ws))
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_speaker_none_mode_runs_the_turn_without_identity():
    """speaker_service=None means identity is off: no buffering, no verify, no
    speaker event -- and the turn still runs (the pre-feature behaviour)."""
    runner = FakeTextRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01"}])
    await bridge._receive_once(ws)
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)
    assert len(runner.calls) == 1
    assert all(e["type"] != "speaker" for e in _sent_json(ws))


class ExplodingSpeaker:
    """Fake SpeakerService whose identify() always raises -- proves a
    model/embed failure can never break the stream (the SAFETY guarantee for
    _verify_utterance)."""

    def identify(self, user_id, pcm, device_hint, auth_is_kadir, allow_adapt=True, cm_ok=None):
        raise RuntimeError("embed blew up")


@pytest.mark.asyncio
async def test_identify_exception_fails_closed_and_the_turn_still_runs(caplog):
    """identify() raising must never propagate: it is logged and fused as
    verified=False/score=0.0, which under presence=locked fails CLOSED to
    trust.LOW -- and the text turn still runs (the policy layer, not the
    bridge, decides what LOW may do)."""
    runner = FakeTextRunner()
    bridge = _armed(VoiceBridge(runner=runner, session_service=None,
                                speaker_service=ExplodingSpeaker(),
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    ws = FakeWS([])
    with caplog.at_level("ERROR"):
        await bridge._handle_text_frame(ws, _user_text("merhaba"))
        await _drive_turns(bridge)

    assert {"type": "speaker", "role": "user", "verified": False, "score": 0.0, "cm_ok": True} in _sent_json(ws)
    assert voice_trust.peek(_trust_key()).trust_level == trust.LOW
    assert "speaker.identify failed" in caplog.text
    assert len(runner.calls) == 1, "a speaker failure killed the text turn"


@pytest.mark.asyncio
async def test_identify_runs_off_the_event_loop_thread():
    """Real ECAPA inference (plus the first-call model load) is seconds of
    blocking CPU: on the loop it would stall this session's audio, its
    receive loop and every other WS connection on the instance."""
    loop_thread = threading.get_ident()
    seen = {}

    class ThreadRecordingSpeaker:
        def identify(self, user_id, pcm, device_hint, auth_is_kadir, allow_adapt=True, cm_ok=None):
            seen["thread"] = threading.get_ident()
            return speaker_mod.IdentifyOutcome(
                verified=True, score=0.9, vec=[0.5], adapted_sample_id=None)

        def record_history(self, user_id, **entry):
            return "h1"

    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=ThreadRecordingSpeaker(),
                                presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._handle_text_frame(FakeWS([]), _user_text("merhaba"))
    await _drive_turns(bridge)
    assert seen["thread"] != loop_thread


@pytest.mark.asyncio
async def test_bridge_records_verification_history_after_the_speaker_event():
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=speaker,
                                device_hint="headset", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._handle_text_frame(FakeWS([]), _user_text("merhaba"))
    await _drive_turns(bridge)

    assert len(speaker.history) == 1
    user_id, entry = speaker.history[0]
    assert entry == {"score": 0.9, "verified": True, "vec": [0.5, 0.5],
                     "device_hint": "headset", "presence": "locked",
                     "trust_level": trust.MEDIUM, "adapted_sample_id": None,
                     "cm_fake_prob": 0.0}


class HistoryExplodingSpeaker(FakeSpeaker):
    """identify works, the history write blows up -- observability must never
    break the safety-relevant outputs (trust publish + client event).

    record_history also snapshots whether the client already had the speaker
    event in hand by the time it fired: a try/except around the history call
    would swallow the RuntimeError either way (that alone doesn't pin
    ordering). This snapshot is what actually catches a reordering."""

    def __init__(self, result, ws):
        super().__init__(result)
        self._ws = ws
        self.event_already_sent_when_called = None

    def record_history(self, user_id, **entry):
        self.event_already_sent_when_called = any(
            isinstance(t, str) and '"speaker"' in t for _, t in self._ws.sent)
        raise RuntimeError("history write blew up")


@pytest.mark.asyncio
async def test_history_write_failure_is_logged_and_does_not_break_the_stream(caplog):
    ws = FakeWS([])
    speaker = HistoryExplodingSpeaker((True, 0.9), ws)
    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=speaker,
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    with caplog.at_level("ERROR"):
        await bridge._handle_text_frame(ws, _user_text("merhaba"))
        await _drive_turns(bridge)

    assert {"type": "speaker", "role": "user", "verified": True, "score": 0.9, "cm_ok": True} in _sent_json(ws)
    assert voice_trust.peek(_trust_key()).trust_level == trust.MEDIUM
    assert "history record failed" in caplog.text
    assert speaker.event_already_sent_when_called is True


@pytest.mark.asyncio
async def test_no_history_row_when_identify_itself_failed():
    """The failure path has no embedding, so there is nothing a correction
    could later feed to the gallery -- no row is written (design decision,
    logged via the existing identify-failure log line)."""
    recorded = []

    class Exploding(ExplodingSpeaker):
        def record_history(self, user_id, **entry):
            recorded.append(entry)

    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None,
                                speaker_service=Exploding(),
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._handle_text_frame(FakeWS([]), _user_text("merhaba"))
    await _drive_turns(bridge)
    assert recorded == []


# --- run(): session, initial trust, teardown ----------------------------------


@pytest.mark.asyncio
async def test_run_snapshots_transcript_on_teardown():
    gate = asyncio.Event()
    ws = ScriptedGatedWS([{"type": "websocket.receive", "text": _user_text("merhaba")}], gate)
    memory = FakeMemory()
    bridge = VoiceBridge(
        runner=FakeTextRunner("selam"), session_service=_StatelessSessions(), memory=memory
    )
    task = asyncio.create_task(bridge.run(ws, user_id="user@example.com"))
    for _ in range(200):
        await asyncio.sleep(0)
        if {"type": "turn_complete"} in _sent_json(ws):
            break
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    assert len(memory.calls) == 1
    session_id, user_id, summary = memory.calls[0]
    assert session_id == "voice-user@example.com"
    assert user_id == "user@example.com"
    assert summary == {"transcript": [
        {"role": "user", "text": "merhaba"},
        {"role": "jarvis", "text": "selam"},
    ]}


@pytest.mark.asyncio
async def test_run_snapshots_last_50_transcript_lines():
    class EchoRunner:
        def run_async(self, *, user_id, session_id, new_message):
            text = new_message.parts[0].text

            async def events():
                yield _FinalResponse(f"cevap:{text}")

            return events()

    gate = asyncio.Event()
    incoming = [
        {"type": "websocket.receive", "text": _user_text(f"line-{i}")}
        for i in range(60)
    ]
    ws = ScriptedGatedWS(incoming, gate)
    memory = FakeMemory()
    bridge = VoiceBridge(
        runner=EchoRunner(), session_service=_StatelessSessions(), memory=memory
    )
    task = asyncio.create_task(bridge.run(ws, user_id="user@example.com"))
    for _ in range(2000):
        await asyncio.sleep(0)
        if sum(1 for e in _sent_json(ws) if e["type"] == "turn_complete") == 60:
            break
    gate.set()
    await asyncio.wait_for(task, timeout=5)

    assert len(memory.calls) == 1
    _, _, summary = memory.calls[0]
    assert len(summary["transcript"]) == 50
    # 120 rows (user+jarvis per turn); the last 50 start mid-turn-35.
    assert summary["transcript"][0] == {"role": "user", "text": "line-35"}
    assert summary["transcript"][-1] == {"role": "jarvis", "text": "cevap:line-59"}


@pytest.mark.asyncio
async def test_run_does_not_snapshot_when_transcript_empty():
    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    memory = FakeMemory()
    bridge = VoiceBridge(
        runner=_NoTurnsRunner(), session_service=_StatelessSessions(), memory=memory
    )
    await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)
    assert memory.calls == []


@pytest.mark.asyncio
async def test_run_without_memory_does_not_crash():
    """memory defaults to None -- teardown must skip the snapshot silently."""
    gate = asyncio.Event()
    ws = ScriptedGatedWS([{"type": "websocket.receive", "text": _user_text("merhaba")}], gate)
    bridge = VoiceBridge(runner=FakeTextRunner("selam"), session_service=_StatelessSessions())
    task = asyncio.create_task(bridge.run(ws, user_id="user@example.com"))
    for _ in range(200):
        await asyncio.sleep(0)
        if {"type": "turn_complete"} in _sent_json(ws):
            break
    gate.set()
    await asyncio.wait_for(task, timeout=2)
    assert bridge.transcript == [
        {"role": "user", "text": "merhaba"},
        {"role": "jarvis", "text": "selam"},
    ]


@pytest.mark.asyncio
async def test_run_swallows_memory_exception_without_masking(caplog):
    """A Firestore hiccup during snapshot must be logged, not raised, so the
    happy path (or an original exception) is never masked."""

    class ExplodingMemory:
        def snapshot_session(self, session_id, user_id, summary):
            raise RuntimeError("firestore hiccup")

    gate = asyncio.Event()
    ws = ScriptedGatedWS([{"type": "websocket.receive", "text": _user_text("merhaba")}], gate)
    bridge = VoiceBridge(
        runner=FakeTextRunner("selam"), session_service=_StatelessSessions(),
        memory=ExplodingMemory(),
    )
    task = asyncio.create_task(bridge.run(ws, user_id="user@example.com"))
    with caplog.at_level("ERROR"):
        for _ in range(200):
            await asyncio.sleep(0)
            if {"type": "turn_complete"} in _sent_json(ws):
                break
        gate.set()
        await asyncio.wait_for(task, timeout=2)
    assert "firestore hiccup" in caplog.text or "snapshot" in caplog.text.lower()


@pytest.mark.asyncio
async def test_teardown_cancels_an_in_flight_turn():
    """A disconnect mid-turn must not leave a turn task running against a dead
    socket (its answers have nowhere to go, and its transcript rows would land
    after the snapshot). Teardown cancels it and run() still returns."""
    started = asyncio.Event()

    class BlockingRunner:
        def run_async(self, **kwargs):
            async def events():
                started.set()
                await asyncio.Event().wait()     # a turn that never finishes
                yield  # pragma: no cover

            return events()

    gate = asyncio.Event()
    ws = ScriptedGatedWS([{"type": "websocket.receive", "text": _user_text("merhaba")}], gate)
    bridge = VoiceBridge(runner=BlockingRunner(), session_service=_StatelessSessions())
    task = asyncio.create_task(bridge.run(ws, user_id="user@example.com"))
    for _ in range(200):
        await asyncio.sleep(0)
        if started.is_set():
            break
    assert started.is_set(), "the turn never started"
    gate.set()
    await asyncio.wait_for(task, timeout=2)
    assert all(t.done() for t in bridge._turn_tasks)


@pytest.mark.asyncio
async def test_run_publishes_connection_initial_trust_before_any_utterance(monkeypatch):
    """A tool call can land before the first utterance is verified. The
    connection's own context must therefore already be published at connection
    start -- locked + no voice evidence yet -> MEDIUM, never the HIGH default.
    Sampled at teardown, which is the last moment the entry still exists."""
    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    published = []
    original_clear = voice_trust.clear
    monkeypatch.setattr(voice_trust, "clear", lambda key, owner: (
        published.append((key, voice_trust.peek(key))), original_clear(key, owner)))

    bridge = VoiceBridge(
        runner=_NoTurnsRunner(), session_service=_StatelessSessions(),
        speaker_service=FakeSpeaker((True, 0.9)),
        presence="locked", device_hint="tablet",
    )
    await asyncio.wait_for(bridge.run(ws, user_id=USER), timeout=2)

    assert published and published[0][0] == _trust_key()
    assert published[0][1].trust_level == trust.MEDIUM
    assert published[0][1].voice_score is None      # no voice evidence yet


@pytest.mark.asyncio
async def test_run_clears_trust_on_teardown():
    """A dead connection must not leak its trust into a later one: the ADK
    session id is per-USER and outlives any single WS connection."""
    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(
        runner=_NoTurnsRunner(), session_service=_StatelessSessions(),
        speaker_service=FakeSpeaker((True, 0.9)),
        presence="locked", device_hint="tablet",
    )
    await asyncio.wait_for(bridge.run(ws, user_id=USER), timeout=2)
    assert voice_trust.peek(_trust_key()) is None


@pytest.mark.asyncio
async def test_run_publishes_nothing_without_speaker_service(monkeypatch):
    """No speaker_service -> identity is off -> no signals -> the policy layer
    keeps defaulting to HIGH, i.e. exactly the pre-feature behaviour."""
    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    seen = []
    monkeypatch.setattr(voice_trust, "publish", lambda key, signals: seen.append(key))
    bridge = VoiceBridge(runner=_NoTurnsRunner(), session_service=_StatelessSessions())
    await asyncio.wait_for(bridge.run(ws, user_id=USER), timeout=2)
    assert seen == []


# --- the handshake: v2 capability gate ---------------------------------------


class FakeHandshakeWS:
    """Duck-type of the subset of fastapi.WebSocket used by _handshake."""

    def __init__(self, text=None, raise_disconnect=False):
        self._text = text
        self._raise_disconnect = raise_disconnect
        self.sent = []
        self.closed_with = None

    async def receive_text(self):
        if self._raise_disconnect:
            raise WebSocketDisconnect(code=1000)
        return self._text

    async def send_text(self, t):
        self.sent.append(t)

    async def close(self, code=1000):
        self.closed_with = code


_CAPS_HELLO = json.dumps({
    "token": "good-token",
    "client_caps": {"stt": "device", "tts": "device", "proto": 2},
})


@pytest.mark.asyncio
async def test_handshake_valid_hello_returns_email(monkeypatch):
    monkeypatch.setattr(voice_mod, "verify_bearer_email", lambda token: "user@example.com")
    ws = FakeHandshakeWS(text=_CAPS_HELLO)
    email, *_ = await _handshake(ws)
    assert email == "user@example.com"
    assert ws.sent == []
    assert ws.closed_with is None


@pytest.mark.asyncio
async def test_handshake_returns_device_hint_and_presence_from_hello(monkeypatch):
    """_handshake's contract is (email, device_hint, presence) so the
    trust-fusion wiring in ws_voice has real values to pass into VoiceBridge --
    this pins the tuple order and that the values actually come from the
    parsed hello, not swapped or hardcoded."""
    monkeypatch.setattr(voice_mod, "verify_bearer_email", lambda token: "user@example.com")
    ws = FakeHandshakeWS(text=json.dumps({
        "token": "good-token", "device_hint": "headset", "presence": "locked",
        "client_caps": {"stt": "device", "tts": "device", "proto": 2},
    }))
    email, device_hint, presence = await _handshake(ws)
    assert (email, device_hint, presence) == ("user@example.com", "headset", "locked")


@pytest.mark.asyncio
async def test_handshake_bad_hello_sends_error_and_closes_4401():
    ws = FakeHandshakeWS(text="not json")
    email = await _handshake(ws)
    assert email is None
    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "error", "message": "Giriş doğrulanamadı"}
    assert ws.closed_with == 4401


@pytest.mark.asyncio
async def test_handshake_disconnect_returns_none_without_raising():
    ws = FakeHandshakeWS(raise_disconnect=True)
    email = await _handshake(ws)
    assert email is None
    assert ws.sent == []
    assert ws.closed_with is None


@pytest.mark.asyncio
async def test_handshake_without_client_caps_is_a_v1_client_and_gets_4409(monkeypatch):
    """A caps-less hello authenticated FINE -- it is just asking for the
    retired Gemini Live bridge. Reject with a DISTINCT code (4409, not 4401)
    so the client can tell "upgrade required" apart from "auth failed"."""
    monkeypatch.setattr(voice_mod, "verify_bearer_email", lambda token: "user@example.com")
    ws = FakeHandshakeWS(text=json.dumps({"token": "good-token"}))
    email = await _handshake(ws)
    assert email is None
    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "error", "message": "Uygulamayı güncelle"}
    assert ws.closed_with == 4409


@pytest.mark.asyncio
async def test_handshake_non_dict_caps_is_not_a_v2_client(monkeypatch):
    """client_caps present but malformed (not an object) must not masquerade
    as a v2 client."""
    monkeypatch.setattr(voice_mod, "verify_bearer_email", lambda token: "user@example.com")
    ws = FakeHandshakeWS(text=json.dumps({"token": "good-token", "client_caps": "device"}))
    email = await _handshake(ws)
    assert email is None
    assert ws.closed_with == 4409


# --- overlapping connections for the same user must not fail open -----------


class _GatedWS(FakeWS):
    """Stays open (yielding to the loop) until `gate` is set, then reports a
    disconnect -- so a second connection can be established while this one is
    still live, which is the whole point of the test below."""

    def __init__(self, gate):
        super().__init__([])
        self.gate = gate

    async def receive(self):
        await self.gate.wait()
        return {"type": "websocket.disconnect"}


@pytest.mark.asyncio
async def test_teardown_of_a_replaced_connection_keeps_the_live_one_s_signals():
    """The trust key is (app_name, user_id, "voice-{user_id}") -- IDENTICAL for
    two concurrent sockets of the same user, which happens routinely when a
    dropped mobile socket lingers while the client already reconnected.

    With an unconditional clear, connection A's teardown deleted connection B's
    LIVE signals and B's next tool call resolved "no signals" -> policy's HIGH
    default: the C1 fail-open signature in a narrower window. clear() is a
    compare-and-delete on the owner token stamped into VoiceSignals.
    """
    key = _trust_key()
    a = VoiceBridge(runner=_NoTurnsRunner(), session_service=_StatelessSessions(),
                    speaker_service=FakeSpeaker((True, 0.9)),
                    presence="locked", device_hint="phone")
    b = VoiceBridge(runner=_NoTurnsRunner(), session_service=_StatelessSessions(),
                    speaker_service=FakeSpeaker((True, 0.9)),
                    presence="locked", device_hint="tablet")
    assert a._owner != b._owner, "connections must be distinguishable in the registry"

    # A is connected and open (its run() published this connection's context).
    gate = asyncio.Event()
    a_task = asyncio.create_task(a.run(_GatedWS(gate), user_id=USER))
    for _ in range(50):
        await asyncio.sleep(0)
        if voice_trust.peek(key) is not None:
            break
    assert voice_trust.peek(key).device_hint == "phone"

    # B reconnects over the SAME key while A's dead socket still lingers.
    _armed(b)
    b._publish_trust(trust.MEDIUM, 0.9)
    assert voice_trust.peek(key).device_hint == "tablet"

    # ...and only THEN does A's socket finally notice it is gone and tear down.
    gate.set()
    await asyncio.wait_for(a_task, timeout=2)

    live = voice_trust.peek(key)
    assert live is not None, (
        "A's teardown wiped B's live signals -- B's next tool call now resolves "
        "to policy's HIGH default (fail-open)")
    assert (live.trust_level, live.device_hint) == (trust.MEDIUM, "tablet")

    # B's own teardown still removes its own entry.
    await asyncio.wait_for(
        b.run(FakeWS([{"type": "websocket.disconnect"}]), user_id=USER), timeout=2)
    assert voice_trust.peek(key) is None


def test_clear_is_a_no_op_for_a_foreign_owner(caplog):
    """Unit-level statement of the same rule, plus the DATA log that makes a
    skipped clear visible instead of mysterious."""
    key = _trust_key()
    voice_trust.publish(key, voice_trust.VoiceSignals(trust_level=trust.LOW, owner="conn-b"))
    with caplog.at_level("INFO"):
        voice_trust.clear(key, "conn-a")
    assert voice_trust.peek(key) is not None
    assert "owned by another live connection" in caplog.text
    voice_trust.clear(key, "conn-b")
    assert voice_trust.peek(key) is None


def test_clear_of_an_absent_key_is_silent():
    voice_trust.clear(_trust_key(), "whoever")          # must not raise


def test_an_unowned_entry_stays_clearable_by_anyone():
    """`owner` defaults to "" so that VoiceSignals stays constructible for the
    many call sites that carry no connection identity (policy tests, fixtures).

    The cost of that default is that a publisher which FORGETS owner= would
    otherwise create an entry no real bridge could ever clear -- a uuid never
    equals "" -- leaking one user's stale trust for the process lifetime. Treat
    the empty token as "unowned": such an entry degrades to the pre-owner
    behaviour (any teardown may clear it) instead of becoming immortal.
    Production always stamps a uuid (voice.py), so this changes nothing there.
    """
    key = _trust_key()
    voice_trust.publish(key, voice_trust.VoiceSignals(trust_level=trust.LOW))
    voice_trust.clear(key, "some-real-connection")
    assert voice_trust.peek(key) is None, (
        "an entry published without an owner can never be cleared -- it leaks "
        "stale trust for the lifetime of the process")


# --- M-b: a misconfigured mic window must not silently remove the bound -----


def test_non_positive_utterance_window_falls_back_to_the_default(caplog):
    """`JARVIS_SPEAKER_UTTERANCE_SECONDS=0` does NOT disable the cap -- voice.py
    drains with `del buf[:-cap]`, and `del buf[:-0]` is `del buf[:0]`, a NO-OP,
    so the buffer would grow unbounded again at 32 KB/s. Anything that rounds
    down to <= 0 bytes is refused loudly and the default window is kept."""
    for bad in (0.0, -5.0, 0.00001):        # 0.00001 s * 16000 * 2 == 0 bytes
        with caplog.at_level("WARNING"):
            assert config._effective_utterance_seconds(bad) == config._DEFAULT_UTTERANCE_SECONDS
        assert "would disable the bound entirely" in caplog.text
        caplog.clear()
    # A usable window is passed through untouched.
    assert config._effective_utterance_seconds(3.0) == 3.0
    # ...and the shipped constant is therefore always a real bound.
    assert config.SPEAKER_UTTERANCE_MAX_BYTES > 0


# --- server-side echo window: the assistant's own TTS may never teach ---------
# Belt-and-braces behind the client's half-duplex guard (VoiceSession). The
# server cannot observe the device's loudspeaker, so it ESTIMATES how long the
# reply takes to say and refuses to ADAPT inside that window. It never changes
# a verification, a trust level or a turn -- only self-feeding.


@pytest.mark.asyncio
async def test_an_utterance_during_the_estimated_speech_window_is_scored_but_never_adapts():
    speaker = FakeSpeaker((True, 0.95))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner("selam"), session_service=None,
                                speaker_service=speaker))
    ws = FakeWS([])
    # A turn happens: the device is now saying "selam" out loud.
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)

    # The echo comes back before the device finished speaking.
    bridge._utterance.extend(b"\x01\x02")
    await bridge._handle_text_frame(ws, _user_text("selam"))
    await _drive_turns(bridge)

    assert speaker.allow_adapt_calls[-1] is False, "echo window must forbid self-feeding"
    # ...but it was still SCORED: trust has to degrade honestly, not silently.
    speaker_events = [e for e in _sent_json(ws) if e["type"] == "speaker"]
    assert speaker_events, "the utterance must still produce a speaker verdict"


@pytest.mark.asyncio
async def test_an_utterance_after_the_window_may_adapt_again():
    speaker = FakeSpeaker((True, 0.95))
    bridge = _armed(VoiceBridge(runner=FakeTextRunner("selam"), session_service=None,
                                speaker_service=speaker))
    ws = FakeWS([])
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)

    # Time passes: the device has finished speaking.
    bridge._speaking_until = 0.0
    bridge._utterance.extend(b"\x03\x04")
    await bridge._handle_text_frame(ws, _user_text("bugün nasılsın"))
    await _drive_turns(bridge)

    assert speaker.allow_adapt_calls[-1] is True


def test_the_speech_estimate_scales_with_the_reply_and_is_clamped():
    # DATA-level: the estimate is the whole guarantee, so its shape is pinned.
    short = VoiceBridge._speech_seconds("selam")            # 5 chars
    long = VoiceBridge._speech_seconds("x" * 140)           # 140 chars -> 10 s + margin
    assert short == pytest.approx(5 / 14.0 + 1.0)
    assert long == pytest.approx(140 / 14.0 + 1.0)
    assert VoiceBridge._speech_seconds("x" * 100_000) == voice_mod.SPEECH_MAX_SECONDS
