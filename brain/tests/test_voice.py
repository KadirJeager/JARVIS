import asyncio
import json
import threading

import pytest
from fastapi import WebSocketDisconnect

import app.voice as voice_mod
from app import config, trust, voice_trust
from app.voice import APP_NAME, VoiceBridge, _handshake

USER = "kadir@example.com"


def _trust_key(user_id=USER):
    return voice_trust.key_for(APP_NAME, user_id, f"voice-{user_id}")


def _armed(bridge, user_id=USER):
    """Give a bridge the session key run() would have established, so unit
    tests that drive _pump_events directly still exercise the real publish
    path instead of _publish_trust's no-op guard."""
    bridge._trust_key = _trust_key(user_id)
    bridge._user_id = user_id
    return bridge


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


class FakeQueue:
    def __init__(self):
        self.blobs = []
        self.closed = False

    def send_realtime(self, blob):
        self.blobs.append(blob)

    def close(self):
        self.closed = True


def _make_event(
    data=None,
    turn_complete=False,
    input_transcription=None,
    output_transcription=None,
):
    """Shape mirrors the ADK live Event surface VoiceBridge reads (verified
    against google/adk/models/llm_response.py in installed google-adk 1.36.2)."""

    class P:
        def __init__(self, data):
            self.inline_data = type("D", (), {"data": data, "mime_type": "audio/pcm"})() if data else None
            self.text = None

    class C:
        def __init__(self, parts):
            self.parts = parts
            self.role = "model"

    class E:
        pass

    e = E()
    e.content = C([P(data)]) if data else None
    e.turn_complete = turn_complete
    e.input_transcription = input_transcription
    e.output_transcription = output_transcription
    return e


class FakeTranscription:
    """Mirrors google.genai.types.Transcription (text: Optional[str], finished:
    Optional[bool]). `finished` defaults False -- i.e. a PARTIAL fragment, which
    is what ADK yields while the user is still speaking
    (models/gemini_llm_connection.py:297-310); only finished=True marks the
    whole-utterance boundary the speaker check may run on."""

    def __init__(self, text, finished=False):
        self.text = text
        self.finished = finished


@pytest.mark.asyncio
async def test_bridge_forwards_mic_bytes_to_queue_and_audio_back():
    q = FakeQueue()

    async def fake_events():
        yield _make_event(data=b"\xaa\xbb")

    ws = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01"}])
    bridge = VoiceBridge(runner=None, session_service=None)
    await bridge._pump_events(fake_events(), ws)          # model -> client
    await bridge._pump_mic_once(ws, q)                    # client -> queue
    assert q.blobs and q.blobs[0].data == b"\x00\x01"
    assert ("bytes", b"\xaa\xbb") in ws.sent


@pytest.mark.asyncio
async def test_bridge_disconnect_closes_queue_and_stops_pump():
    q = FakeQueue()
    ws = FakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(runner=None, session_service=None)
    keep_going = await bridge._pump_mic_once(ws, q)
    assert keep_going is False
    assert q.closed is True


@pytest.mark.asyncio
async def test_bridge_emits_turn_complete_event():
    async def fake_events():
        yield _make_event(turn_complete=True)

    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None)
    await bridge._pump_events(fake_events(), ws)
    assert ("text", json.dumps({"type": "turn_complete"})) in ws.sent


@pytest.mark.asyncio
async def test_bridge_emits_transcript_events_for_input_and_output():
    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba"))
        yield _make_event(output_transcription=FakeTranscription("selam"))

    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None)
    await bridge._pump_events(fake_events(), ws)
    assert ("text", json.dumps({"type": "transcript", "role": "user", "text": "merhaba"})) in ws.sent
    assert ("text", json.dumps({"type": "transcript", "role": "jarvis", "text": "selam"})) in ws.sent


class YieldingFakeWS(FakeWS):
    """Like FakeWS, but yields to the event loop on every receive(), the way a
    real ASGI socket read always would -- this lets the concurrently running
    _pump_events task actually get scheduled before the mic loop exits."""

    async def receive(self):
        await asyncio.sleep(0)
        return await super().receive()

    async def close(self, code=1000):
        self.sent.append(("close", code))


class FakeMemory:
    """Fake Memory: records snapshot_session calls without touching Firestore."""

    def __init__(self):
        self.calls = []

    def snapshot_session(self, session_id, user_id, summary):
        self.calls.append((session_id, user_id, summary))


@pytest.mark.asyncio
async def test_pump_events_accumulates_transcript():
    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba"))
        yield _make_event(output_transcription=FakeTranscription("selam"))

    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None)
    await bridge._pump_events(fake_events(), ws)
    assert bridge.transcript == [
        {"role": "user", "text": "merhaba"},
        {"role": "jarvis", "text": "selam"},
    ]


@pytest.mark.asyncio
async def test_run_snapshots_transcript_on_teardown():
    class OneShotRunner:
        def run_live(self, **kwargs):
            async def events():
                yield _make_event(output_transcription=FakeTranscription("selam"))

            return events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    ws = YieldingFakeWS(
        [
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.disconnect"},
        ]
    )
    memory = FakeMemory()
    bridge = VoiceBridge(
        runner=OneShotRunner(), session_service=FakeSessionService(), memory=memory
    )
    await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)

    assert len(memory.calls) == 1
    session_id, user_id, summary = memory.calls[0]
    assert session_id == "voice-user@example.com"
    assert user_id == "user@example.com"
    assert summary == {"transcript": [{"role": "jarvis", "text": "selam"}]}


@pytest.mark.asyncio
async def test_run_snapshots_last_50_transcript_lines():
    class ManyLinesRunner:
        def run_live(self, **kwargs):
            async def events():
                for i in range(60):
                    yield _make_event(output_transcription=FakeTranscription(f"line-{i}"))

            return events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    memory = FakeMemory()
    bridge = VoiceBridge(
        runner=ManyLinesRunner(), session_service=FakeSessionService(), memory=memory
    )
    await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)

    assert len(memory.calls) == 1
    _, _, summary = memory.calls[0]
    assert len(summary["transcript"]) == 50
    assert summary["transcript"][0] == {"role": "jarvis", "text": "line-10"}
    assert summary["transcript"][-1] == {"role": "jarvis", "text": "line-59"}


@pytest.mark.asyncio
async def test_run_does_not_snapshot_when_transcript_empty():
    class NoTranscriptRunner:
        def run_live(self, **kwargs):
            async def events():
                yield _make_event(data=b"\x01")

            return events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    ws = FakeWS([{"type": "websocket.disconnect"}])
    memory = FakeMemory()
    bridge = VoiceBridge(
        runner=NoTranscriptRunner(), session_service=FakeSessionService(), memory=memory
    )
    await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)

    assert memory.calls == []


@pytest.mark.asyncio
async def test_run_without_memory_does_not_crash():
    """memory defaults to None -- teardown must skip the snapshot silently."""

    class OneShotRunner:
        def run_live(self, **kwargs):
            async def events():
                yield _make_event(output_transcription=FakeTranscription("selam"))

            return events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(runner=OneShotRunner(), session_service=FakeSessionService())
    await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)
    assert bridge.transcript == [{"role": "jarvis", "text": "selam"}]


@pytest.mark.asyncio
async def test_run_snapshots_even_when_run_exits_via_exception():
    """Snapshot must survive an exception path -- outermost finally, not
    just the happy path -- and a memory hiccup must not mask the original
    exception."""

    class FailingRunner:
        def run_live(self, **kwargs):
            async def failing_events():
                yield _make_event(output_transcription=FakeTranscription("merhaba"))
                raise RuntimeError("live stream died")

            return failing_events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    ws = YieldingFakeWS(
        [
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.disconnect"},
        ]
    )
    memory = FakeMemory()
    bridge = VoiceBridge(runner=FailingRunner(), session_service=FakeSessionService(), memory=memory)
    with pytest.raises(RuntimeError, match="live stream died"):
        await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)

    assert len(memory.calls) == 1
    assert memory.calls[0][2] == {"transcript": [{"role": "jarvis", "text": "merhaba"}]}


@pytest.mark.asyncio
async def test_run_swallows_memory_exception_without_masking_original(monkeypatch, caplog):
    """A Firestore hiccup during snapshot must be logged, not raised, so the
    original exception (or the happy path) is never masked."""

    class OneShotRunner:
        def run_live(self, **kwargs):
            async def events():
                yield _make_event(output_transcription=FakeTranscription("selam"))

            return events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    class ExplodingMemory:
        def snapshot_session(self, session_id, user_id, summary):
            raise RuntimeError("firestore hiccup")

    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(
        runner=OneShotRunner(), session_service=FakeSessionService(), memory=ExplodingMemory()
    )
    with caplog.at_level("ERROR"):
        await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)
    assert "firestore hiccup" in caplog.text or "snapshot" in caplog.text.lower()


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


@pytest.mark.asyncio
async def test_handshake_valid_hello_returns_email(monkeypatch):
    monkeypatch.setattr(voice_mod, "verify_token_email", lambda token: "user@example.com")
    ws = FakeHandshakeWS(text=json.dumps({"token": "good-token"}))
    email, *_ = await _handshake(ws)
    assert email == "user@example.com"
    assert ws.sent == []
    assert ws.closed_with is None


@pytest.mark.asyncio
async def test_handshake_returns_device_hint_and_presence_from_hello(monkeypatch):
    """_handshake's contract grew from `email` to `(email, device_hint,
    presence)` (Task 10) so the trust-fusion wiring in ws_voice has real
    values to pass into VoiceBridge -- this pins the tuple order and that the
    values actually come from the parsed hello, not swapped or hardcoded."""
    monkeypatch.setattr(voice_mod, "verify_token_email", lambda token: "user@example.com")
    ws = FakeHandshakeWS(
        text=json.dumps({"token": "good-token", "device_hint": "headset", "presence": "locked"})
    )
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
async def test_run_stops_and_propagates_when_pump_events_dies_without_hanging():
    """If _pump_events raises after yielding once, run() must: (1) not hang
    (bounded by wait_for), (2) not keep pumping mic audio into a dead
    session past the point _pump_events died, and (3) have its exception
    actually retrieved (no 'Task exception was never retrieved' warning from
    asyncio's default handler, verified via a custom exception_handler)."""

    class FailingRunner:
        def run_live(self, **kwargs):
            async def failing_events():
                yield _make_event(data=b"\x01")
                raise RuntimeError("live stream died")

            return failing_events()

    class FakeSessionService:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    class YieldingFakeWS(FakeWS):
        """Like FakeWS, but yields to the event loop on every receive(), the
        way a real ASGI socket read always would -- this lets the concurrently
        running _pump_events task actually get scheduled and fail."""

        async def receive(self):
            await asyncio.sleep(0)
            return await super().receive()

        async def close(self, code=1000):
            self.sent.append(("close", code))

    ws = YieldingFakeWS(
        [
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.disconnect"},
        ]
    )
    bridge = VoiceBridge(runner=FailingRunner(), session_service=FakeSessionService())

    loop = asyncio.get_running_loop()
    unhandled = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))
    try:
        with pytest.raises(RuntimeError, match="live stream died"):
            await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)
    finally:
        loop.set_exception_handler(previous_handler)

    assert unhandled == []
    # The dead session must not have absorbed every queued mic message --
    # the pump_out.done() check should have cut the loop short.
    assert ws.incoming, "run() kept draining mic input after the live stream died"


async def test_events_generator_closed_even_when_pump_dies_of_send_failure():
    """Regression for the mid-finally control-flow bug: when _pump_events dies
    of its OWN exception (ws send failure) while the events generator is still
    suspended, awaiting pump_out re-raises inside finally -- the nested
    finally must STILL aclose() the generator (proven via its finally flag)."""
    closed = {"v": False}

    class InfiniteRunner:
        def run_live(self, **kwargs):
            async def events():
                try:
                    while True:
                        yield _make_event(data=b"\x01")
                        await asyncio.sleep(0)
                finally:
                    closed["v"] = True

            return events()

    class SessionSvc:
        async def get_session(self, **kwargs):
            return object()

        async def create_session(self, **kwargs):
            return object()

    class SendFailingWS(FakeWS):
        async def receive(self):
            await asyncio.sleep(0)
            return await super().receive()

        async def send_bytes(self, b):
            raise RuntimeError("send failed")

        async def close(self, code=1000):
            self.sent.append(("close", code))

    ws = SendFailingWS([
        {"type": "websocket.receive", "bytes": b"\x00"},
        {"type": "websocket.receive", "bytes": b"\x00"},
        {"type": "websocket.receive", "bytes": b"\x00"},
        {"type": "websocket.disconnect"},
    ])
    bridge = VoiceBridge(runner=InfiniteRunner(), session_service=SessionSvc())
    with pytest.raises(RuntimeError, match="send failed"):
        await asyncio.wait_for(bridge.run(ws, user_id="user@example.com"), timeout=2)
    assert closed["v"], "events generator was not aclosed on pump failure"


class FakeSpeaker:
    """Fake SpeakerService: canned (verified, score) result, records calls."""

    def __init__(self, result):  # (verified, score)
        self.result = result
        self.calls = []

    def identify(self, user_id, pcm, device_hint, auth_is_kadir):
        self.calls.append((user_id, pcm, device_hint, auth_is_kadir))
        return self.result


@pytest.mark.asyncio
async def test_bridge_verifies_utterance_and_publishes_signals_and_event():
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(data=b"\x00\x01")                       # mic-ish (ignored here)
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None, speaker_service=speaker,
                                device_hint="headset", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")            # buffered mic audio
    await bridge._pump_events(fake_events(), ws)

    assert speaker.calls and speaker.calls[0][2] == "headset"     # identify called w/ device
    # locked + verified -> MEDIUM, carried to the policy layer with the WHY
    # fields the audit trail needs (spec §7).
    signals = voice_trust.peek(_trust_key())
    assert signals is not None
    assert (signals.trust_level, signals.voice_score, signals.presence, signals.device_hint) == (
        trust.MEDIUM, 0.9, "locked", "headset")
    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": True, "score": 0.9})) in ws.sent


@pytest.mark.asyncio
async def test_bridge_speaker_none_is_noop():
    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("selam"))
    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None)       # no speaker_service
    await bridge._pump_events(fake_events(), ws)                  # must not raise
    # only the transcript event, no speaker event
    assert all("speaker" not in t for _, t in ws.sent if isinstance(t, str))


@pytest.mark.asyncio
async def test_pump_mic_once_buffers_utterance_only_when_speaker_service_set():
    """Exercises the buffering guard through the REAL entry point
    (_pump_mic_once), not by hand-setting bridge._utterance like the tests
    above -- this is what actually proves the `is not None` check is
    load-bearing (an inverted `is None` guard would still pass every other
    test in this file but fail this one)."""
    speaker = FakeSpeaker((True, 0.9))
    with_speaker = VoiceBridge(runner=None, session_service=None, speaker_service=speaker)
    ws = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01\x02\x03"}])
    await with_speaker._pump_mic_once(ws, FakeQueue())
    assert bytes(with_speaker._utterance) == b"\x00\x01\x02\x03"

    without_speaker = VoiceBridge(runner=None, session_service=None)  # speaker_service=None
    ws2 = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01\x02\x03"}])
    await without_speaker._pump_mic_once(ws2, FakeQueue())
    assert bytes(without_speaker._utterance) == b""


class ExplodingSpeaker:
    """Fake SpeakerService whose identify() always raises -- proves a
    model/embed failure can never break the audio stream (the brief's SAFETY
    guarantee for _verify_utterance)."""

    def identify(self, user_id, pcm, device_hint, auth_is_kadir):
        raise RuntimeError("embed blew up")


@pytest.mark.asyncio
async def test_verify_utterance_treats_identify_exception_as_unverified_and_fails_closed(caplog):
    """identify() raising must never propagate out of _pump_events: it is
    logged and fused as verified=False/score=0.0, which under presence=locked
    fails CLOSED to trust.LOW (not silently HIGH/MEDIUM) -- this is the path
    Task 10 makes reachable by real mic traffic, so it must be proven here."""

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=ExplodingSpeaker(),
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")             # buffered mic audio

    with caplog.at_level("ERROR"):
        await bridge._pump_events(fake_events(), ws)                # must not raise

    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": False, "score": 0.0})) in ws.sent
    assert voice_trust.peek(_trust_key()).trust_level == trust.LOW
    assert "speaker.identify failed" in caplog.text


class _NoEvents:
    def run_live(self, **kwargs):
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


@pytest.mark.asyncio
async def test_run_publishes_connection_initial_trust_before_any_utterance(monkeypatch):
    """A tool call can land before the first utterance is verified (ADK does not
    order transcription ahead of tool_call). The connection's own context must
    therefore already be published at connection start -- locked + no voice
    evidence yet -> MEDIUM, never the HIGH default. Sampled at teardown, which
    is the last moment the entry still exists."""
    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    published = []
    original_clear = voice_trust.clear
    monkeypatch.setattr(voice_trust, "clear", lambda key, owner: (
        published.append((key, voice_trust.peek(key))), original_clear(key, owner)))

    bridge = VoiceBridge(
        runner=_NoEvents(), session_service=_StatelessSessions(),
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
        runner=_NoEvents(), session_service=_StatelessSessions(),
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
    bridge = VoiceBridge(runner=_NoEvents(), session_service=_StatelessSessions())
    await asyncio.wait_for(bridge.run(ws, user_id=USER), timeout=2)
    assert seen == []


# --- I1: the utterance buffer must be bounded -------------------------------


@pytest.mark.asyncio
async def test_utterance_buffer_is_capped_to_the_configured_window(monkeypatch):
    """The buffer is only drained at a turn boundary, and a turn boundary is not
    guaranteed to arrive (long silence, model hiccup, transcription misconfig).
    Unbounded it grows at 32 KB/s in a torch-carrying process. It must keep the
    most RECENT window, not the oldest -- the newest audio is the utterance the
    next verification is about."""
    monkeypatch.setattr(config, "SPEAKER_UTTERANCE_MAX_BYTES", 8)
    bridge = VoiceBridge(runner=None, session_service=None,
                         speaker_service=FakeSpeaker((True, 0.9)))
    q = FakeQueue()
    for chunk in (b"aaaa", b"bbbb", b"cccc", b"dddd"):
        ws = FakeWS([{"type": "websocket.receive", "bytes": chunk}])
        await bridge._pump_mic_once(ws, q)

    assert bytes(bridge._utterance) == b"ccccdddd"        # newest 8 bytes only
    # every chunk still reached the live model -- capping is buffer-only
    assert [b.data for b in q.blobs] == [b"aaaa", b"bbbb", b"cccc", b"dddd"]


def test_configured_window_is_ten_seconds_of_contract_audio():
    """Pins the unit: SPEAKER_UTTERANCE_MAX_BYTES is BYTES derived from seconds
    x the protocol's input rate x 2 bytes/sample, so the seconds knob and the
    byte cap can never drift apart."""
    assert config.SPEAKER_UTTERANCE_MAX_BYTES == int(
        config.SPEAKER_UTTERANCE_SECONDS * voice_mod.vp.AUDIO_IN_RATE * 2)
    assert config.SPEAKER_UTTERANCE_MAX_BYTES == 320000    # 10 s @ 16 kHz PCM16


# --- I2: inference must not block the event loop ----------------------------


@pytest.mark.asyncio
async def test_identify_runs_off_the_event_loop_thread():
    """Real ECAPA inference (plus the first-call model load) is seconds of
    blocking CPU: on the loop it would stall this session's audio, its mic pump
    and every other WS connection on the instance."""
    loop_thread = threading.get_ident()
    seen = {}

    class ThreadRecordingSpeaker:
        def identify(self, user_id, pcm, device_hint, auth_is_kadir):
            seen["thread"] = threading.get_ident()
            return True, 0.9

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=ThreadRecordingSpeaker(),
                                presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._pump_events(fake_events(), FakeWS([]))
    assert seen["thread"] != loop_thread


# --- I3: verify once per turn, at the utterance boundary --------------------


@pytest.mark.asyncio
async def test_partial_transcriptions_do_not_trigger_verification():
    """ADK yields incremental input transcriptions with finished=False while the
    user is still speaking (models/gemini_llm_connection.py:297-310). Verifying
    on those scores an arbitrary sub-second fragment against thresholds
    calibrated on ~3 s clips."""
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("mer"))
        yield _make_event(input_transcription=FakeTranscription("merhaba nasil"))

    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    ws = FakeWS([])
    await bridge._pump_events(fake_events(), ws)

    assert speaker.calls == []                              # never verified
    assert bytes(bridge._utterance) == b"\x00\x01\x02\x03"  # buffer NOT drained
    # transcripts still forwarded to the client -- only verification is gated
    assert sum(1 for _, t in ws.sent if "transcript" in t) == 2


@pytest.mark.asyncio
async def test_verifies_once_at_the_finished_transcription_of_each_turn():
    """One verification per turn, on the whole-utterance event -- and the next
    turn verifies again (the per-turn latch must reset at turn_complete)."""
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("mer"))
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))
        yield _make_event(turn_complete=True)
        yield _make_event(input_transcription=FakeTranscription("ikinci tur", finished=True))

    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"turn-one-audio")
    await bridge._pump_events(fake_events(), FakeWS([]))
    assert len(speaker.calls) == 1, "a second finished event in the same turn re-verified"

    bridge._utterance = bytearray(b"turn-two-audio")
    await bridge._pump_events(fake_events(), FakeWS([]))
    assert len(speaker.calls) == 2
    assert speaker.calls[-1][1] == b"turn-two-audio"


@pytest.mark.asyncio
async def test_turn_complete_verifies_when_no_finished_transcription_arrived():
    """ADK itself flushes pending transcriptions on turn_complete because "the
    Gemini API or Vertex AI might not send a transcription finished signal"
    (models/gemini_llm_connection.py:349-367). If that flush never produces one
    either, turn_complete is still a turn boundary -- verify what was buffered
    instead of silently skipping the turn."""
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("yarim"))
        yield _make_event(turn_complete=True)

    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._pump_events(fake_events(), FakeWS([]))
    assert len(speaker.calls) == 1
    assert speaker.calls[0][1] == b"\x00\x01\x02\x03"


@pytest.mark.asyncio
async def test_turn_complete_with_empty_buffer_does_not_verify():
    """The fallback must not fire on a model-only turn (nothing was spoken):
    _verify_utterance's empty-buffer early return covers it, so no identify
    call and no speaker event."""
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(turn_complete=True)

    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    ws = FakeWS([])
    await bridge._pump_events(fake_events(), ws)
    assert speaker.calls == []
    assert all("speaker" not in t for _, t in ws.sent if isinstance(t, str))


@pytest.mark.asyncio
async def test_latch_rearms_when_new_mic_audio_arrives_after_a_drain():
    """M-c: `_verified_this_turn` must not be able to stick.

    It is cleared at turn_complete -- but ADK also flushes pending
    transcriptions on `interrupted` (barge-in), and that path can yield a
    finished input transcription with NO turn_complete after it
    (models/gemini_llm_connection.py:349-367 flushes on interrupted too). The
    latch would then stay set and the NEXT turn would never be verified: a
    silent, security-relevant miss. Re-arming when the drained buffer refills
    closes it, because fresh mic bytes are by definition a new utterance.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))

    # Turn 1: finished transcription -> verified, buffer drained, latch SET.
    async def turn_one():
        yield _make_event(input_transcription=FakeTranscription("birinci", finished=True))

    bridge._utterance = bytearray(b"turn-one-audio")
    await bridge._pump_events(turn_one(), FakeWS([]))
    assert len(speaker.calls) == 1
    assert bridge._verified_this_turn is True          # no turn_complete arrived
    assert bytes(bridge._utterance) == b""

    # Turn 2 begins the only way it can: new mic bytes through the real pump.
    ws = FakeWS([{"type": "websocket.receive", "bytes": b"turn-two-audio"}])
    await bridge._pump_mic_once(ws, FakeQueue())
    assert bridge._verified_this_turn is False, (
        "the latch stayed set after the buffer refilled -- turn 2 can never be verified")

    async def turn_two():
        yield _make_event(input_transcription=FakeTranscription("ikinci", finished=True))

    await bridge._pump_events(turn_two(), FakeWS([]))
    assert len(speaker.calls) == 2
    assert speaker.calls[-1][1] == b"turn-two-audio"


@pytest.mark.asyncio
async def test_mid_utterance_audio_does_not_rearm_the_latch():
    """The re-arm must key on "the buffer was empty", not "bytes arrived":
    otherwise every mic frame would clear the latch and a long utterance would
    be re-verified on each ADK-flushed finished transcription."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._verified_this_turn = True
    bridge._utterance = bytearray(b"already-buffered")

    ws = FakeWS([{"type": "websocket.receive", "bytes": b"more"}])
    await bridge._pump_mic_once(ws, FakeQueue())

    assert bridge._verified_this_turn is True
    assert bytes(bridge._utterance) == b"already-bufferedmore"


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
    default: the C1 fail-open signature in a narrower window. clear() is now a
    compare-and-delete on the owner token stamped into VoiceSignals.
    """
    key = _trust_key()
    a = VoiceBridge(runner=_NoEvents(), session_service=_StatelessSessions(),
                    speaker_service=FakeSpeaker((True, 0.9)),
                    presence="locked", device_hint="phone")
    b = VoiceBridge(runner=_NoEvents(), session_service=_StatelessSessions(),
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
