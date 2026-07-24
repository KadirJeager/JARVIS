import asyncio
import json
import threading

import pytest
from fastapi import WebSocketDisconnect

import app.voice as voice_mod
from app import config, speaker as speaker_mod, trust, voice_protocol as vp, voice_trust
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
    interrupted=False,
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
    # Event extends LlmResponse (events/event.py:31) and _finalize_model_response_event
    # merges the LlmResponse dump into it, so `interrupted` reaches us as an Event
    # attribute (models/llm_response.py:99).
    e.interrupted = interrupted
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
    """Fake SpeakerService: canned IdentifyOutcome, records identify AND
    record_history calls."""

    def __init__(self, result):  # (verified, score)
        verified, score = result
        self.result = speaker_mod.IdentifyOutcome(
            verified=verified, score=score, vec=[0.5, 0.5],
            adapted_sample_id=None)
        self.calls = []
        self.history = []

    def identify(self, user_id, pcm, device_hint, auth_is_kadir):
        self.calls.append((user_id, pcm, device_hint, auth_is_kadir))
        return self.result

    def record_history(self, user_id, **entry):
        self.history.append((user_id, entry))
        return "h1"


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


@pytest.mark.asyncio
async def test_bridge_records_verification_history_after_the_speaker_event():
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None, speaker_service=speaker,
                                device_hint="headset", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._pump_events(fake_events(), ws)

    assert len(speaker.history) == 1
    user_id, entry = speaker.history[0]
    assert entry == {"score": 0.9, "verified": True, "vec": [0.5, 0.5],
                     "device_hint": "headset", "presence": "locked",
                     "trust_level": trust.MEDIUM, "adapted_sample_id": None}


class HistoryExplodingSpeaker(FakeSpeaker):
    """identify works, the history write blows up -- observability must never
    break the safety-relevant outputs (trust publish + client event).

    record_history also snapshots whether the client already had the speaker
    event in hand by the time it fired: a try/except around the history call
    would swallow the RuntimeError either way (that alone doesn't pin
    ordering -- proven empirically: moving the history block before the
    publish/send left the other three asserts below green). This snapshot is
    what actually catches a reordering."""

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
    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    speaker = HistoryExplodingSpeaker((True, 0.9), ws)
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker,
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    with caplog.at_level("ERROR"):
        await bridge._pump_events(fake_events(), ws)          # must not raise

    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": True, "score": 0.9})) in ws.sent
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

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=Exploding(),
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._pump_events(fake_events(), ws)
    assert recorded == []


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
    turn verifies again (the per-turn latch must reset at turn_complete).

    Turn two's audio is buffered AFTER turn_complete, which is the only way it
    happens: turn_complete drains whatever the open mic collected while the
    model was speaking, so audio buffered BEFORE it belongs to turn one and is
    deliberately not carried forward (see
    test_a_verified_turn_drains_the_inter_turn_audio)."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("mer"))
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))
        yield _make_event(turn_complete=True)
        bridge._utterance.extend(b"turn-two-audio")     # the mic, during turn two
        yield _make_event(input_transcription=FakeTranscription("ikinci tur", finished=True))

    bridge._utterance = bytearray(b"turn-one-audio")
    await bridge._pump_events(fake_events(), FakeWS([]))

    assert len(speaker.calls) == 2, "a second finished event in the same turn re-verified"
    assert speaker.calls[0][1] == b"turn-one-audio"
    assert speaker.calls[1][1] == b"turn-two-audio"


@pytest.mark.asyncio
async def test_turn_complete_verifies_when_no_finished_transcription_arrived():
    """ADK itself flushes pending transcriptions on turn_complete because "the
    Gemini API or Vertex AI might not send a transcription finished signal"
    (models/gemini_llm_connection.py:349-367). If that flush never produces one
    either, turn_complete is still a turn boundary -- verify what was buffered
    instead of silently skipping the turn.

    The buffer here is a real utterance's worth of audio: this path now carries
    a minimum-length floor (see test_turn_complete_fallback_skips_a_sub_floor_
    fragment), so the token 4-byte buffer this test used before the floor
    existed would exercise the skip, not the fallback it is about."""
    speaker = FakeSpeaker((True, 0.9))
    pcm = b"\x00\x01\x02\x03" * (config.SPEAKER_MIN_UTTERANCE_BYTES // 4)

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("yarim"))
        yield _make_event(turn_complete=True)

    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(pcm)
    await bridge._pump_events(fake_events(), FakeWS([]))
    assert len(speaker.calls) == 1
    assert speaker.calls[0][1] == pcm


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
async def test_post_utterance_mic_frames_do_not_cause_a_second_verification():
    """ONE verification per turn, driven through the REAL interleaving.

    The mic is open for the whole turn -- web/app.js:151-153 forwards every
    worklet buffer unconditionally, there is no VAD gate -- and _pump_events
    runs CONCURRENTLY with the mic loop (voice.py: both are tasks). So between
    the finished transcription (which drains the buffer) and turn_complete
    (which arrives only after the model has finished speaking, seconds later)
    the buffer necessarily refills with post-utterance room noise.

    Any latch scheme that re-arms on that refill turns the turn_complete
    fallback into a SECOND identify() call -- on noise. ECAPA on sub-second
    noise returns verified=False, and trust.assess then fuses LOW for
    locked/ambient, overwriting the correct level computed moments earlier.
    That is a false reject on the normal path, on exactly the presence modes
    voice identity exists for.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"the-real-utterance")

    async def finished():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    await bridge._pump_events(finished(), FakeWS([]))       # verify + drain

    # The model is now speaking; the open mic keeps streaming into the drained
    # buffer. turn_complete only arrives once the model has finished, so this is
    # SECONDS of room noise -- comfortably above the fallback's minimum-length
    # floor, which is why that floor alone cannot stand in for this fix.
    noise = b"\x00\x01" * config.SPEAKER_MIN_UTTERANCE_BYTES
    ws = FakeWS([{"type": "websocket.receive", "bytes": noise}])
    await bridge._pump_mic_once(ws, FakeQueue())

    async def turn_end():
        yield _make_event(turn_complete=True)

    await bridge._pump_events(turn_end(), FakeWS([]))

    assert len(speaker.calls) == 1, (
        f"ECAPA re-ran on post-utterance audio {speaker.calls[1:]!r}: the turn's "
        "correct trust level is overwritten by a false reject")


@pytest.mark.asyncio
async def test_interrupted_rearms_the_latch_for_the_next_turn():
    """The barge-in case M-c was aimed at, keyed on the signal that actually
    reports it instead of on a proxy.

    ADK flushes pending transcriptions on `interrupted` as well as on
    turn_complete (models/gemini_llm_connection.py:349-367), so a barged-into
    turn can end with a finished input transcription and NO turn_complete. The
    latch is cleared at turn_complete only, so without handling `interrupted`
    it stays set and the NEXT turn is never verified -- a silent security miss.

    `interrupted` reaches us because RunConfig.save_live_blob defaults to False
    (agents/run_config.py:258); with it enabled ADK returns early from the
    control-event flush branch (flows/llm_flows/base_llm_flow.py:1121-1134) and
    never yields the event. If that config ever changes, this test is the guard.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))

    # Turn 1 ends by barge-in: finished transcription, then interrupted, no turn_complete.
    bridge._utterance = bytearray(b"turn-one-audio")

    async def barged_into_turn():
        yield _make_event(input_transcription=FakeTranscription("birinci", finished=True))
        yield _make_event(interrupted=True)

    await bridge._pump_events(barged_into_turn(), FakeWS([]))
    assert len(speaker.calls) == 1
    assert bridge._verified_this_turn is False, (
        "the latch stuck after a barge-in -- the next turn can never be verified")

    bridge._utterance = bytearray(b"turn-two-audio")

    async def turn_two():
        yield _make_event(input_transcription=FakeTranscription("ikinci", finished=True))

    await bridge._pump_events(turn_two(), FakeWS([]))
    assert len(speaker.calls) == 2
    assert speaker.calls[-1][1] == b"turn-two-audio"


@pytest.mark.parametrize("shape", ["one event carrying both flags", "interrupted then turn_complete"])
@pytest.mark.asyncio
async def test_a_barge_in_turn_does_not_run_the_fallback(shape):
    """`interrupted` re-arms the latch -- so the turn_complete in the SAME turn
    must not then read that re-armed latch as "this turn was never verified".

    ADK produces both flags on ONE response
    (models/gemini_llm_connection.py:396-398:
    `LlmResponse(turn_complete=True, interrupted=..., ...)`), and can also emit
    them as separate events. Either way, clearing the latch and immediately
    consulting it re-creates C-1 exactly: a second identify() on the audio the
    open mic buffered while the model was speaking -- which under locked/ambient
    publishes LOW over the turn's correct level. Barge-in is a routine
    interaction, not an edge case.

    What is in the buffer at a barge-in belongs to the NEXT utterance, so it is
    neither scored nor dropped here; it gets verified at its own boundary.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"turn-one-audio")

    barge_in_audio = b"\x07\x08" * config.SPEAKER_MIN_UTTERANCE_BYTES

    async def turn():
        yield _make_event(input_transcription=FakeTranscription("birinci", finished=True))
        # The model is speaking; the user talks over it and the mic buffers that.
        bridge._utterance.extend(barge_in_audio)
        if shape == "one event carrying both flags":
            yield _make_event(turn_complete=True, interrupted=True)
        else:
            yield _make_event(interrupted=True)
            yield _make_event(turn_complete=True)

    await bridge._pump_events(turn(), FakeWS([]))

    assert len(speaker.calls) == 1, (
        f"the barge-in turn verified twice ({shape}) -- the second call scored "
        f"{speaker.calls[1][1][:16]!r}..., audio buffered while the model spoke")
    assert bytes(bridge._utterance) == barge_in_audio[-config.SPEAKER_BARGE_IN_ONSET_BYTES:], (
        "the barge-in utterance was dropped instead of left, trimmed to its "
        "onset window, for its own boundary")
    assert bridge._verified_this_turn is False, "the next turn must still verify"


@pytest.mark.asyncio
async def test_a_barge_in_without_turn_complete_does_not_disarm_the_next_turn():
    """A barge-in turn can end with NO turn_complete at all -- that is the whole
    reason `interrupted` is handled. So nothing about "this turn was barged
    into" may be carried on a flag that only turn_complete resets: the next
    turn's turn_complete would then read the PREVIOUS turn's barge-in and skip
    both its drain and its fallback.

    Both halves of that are asserted: turn N+1 still drains (else inter-turn
    audio prefixes N+2), and a turn N+1 that never gets a finished transcription
    still runs the fallback (else it passes unverified -- fail-open).
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))

    # Turn N: verified, then barged into, and NO turn_complete ever arrives.
    bridge._utterance = bytearray(b"turn-n-audio")

    async def turn_n():
        yield _make_event(input_transcription=FakeTranscription("n", finished=True))
        bridge._utterance.extend(b"\x05\x06" * config.SPEAKER_MIN_UTTERANCE_BYTES)
        yield _make_event(interrupted=True)

    await bridge._pump_events(turn_n(), FakeWS([]))
    assert len(speaker.calls) == 1

    # Turn N+1: the barge-in utterance reaches its own boundary and is scored,
    # then the turn ends normally -- which must drain.
    async def turn_n_plus_1():
        yield _make_event(input_transcription=FakeTranscription("n+1", finished=True))
        bridge._utterance.extend(b"\x00\x01" * config.SPEAKER_MIN_UTTERANCE_BYTES)
        yield _make_event(turn_complete=True)

    await bridge._pump_events(turn_n_plus_1(), FakeWS([]))
    assert len(speaker.calls) == 2
    assert bytes(bridge._utterance) == b"", (
        "turn N's barge-in suppressed turn N+1's drain -- inter-turn audio will "
        "prefix turn N+2's utterance")


@pytest.mark.asyncio
async def test_the_pending_claim_survives_a_turn_with_no_transcription_at_all():
    """PINS A KNOWN, BOUNDED RESIDUAL rather than leaving it to be rediscovered.

    A pending claim is settled only by a verification consuming the buffer. So
    if a barge-in is followed by a turn in which Gemini sends NO input
    transcription whatsoever, that turn's turn_complete also declines to run the
    fallback, and the level published for the previous utterance stands.

    That combination needs two independently uncommon things at once (a barge-in,
    then a turn with zero transcriptions, while input transcription is enabled
    and Gemini 3.x Live sends whole-utterance ones). The alternative -- adding a
    signal that expires the claim -- was rejected: every previous fix to this
    state machine added a flag whose reset depended on an event that is not
    guaranteed, and that is precisely how the last three defects were built.

    It fails to a level MEASURED from real audio, never to a fabricated HIGH:
    the connection baseline is already MEDIUM under locked/ambient. If this ever
    shows up in practice, the fix is a real VAD/energy gate, not another flag.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"turn-n-audio")

    async def barge_in_only():
        yield _make_event(input_transcription=FakeTranscription("n", finished=True))
        yield _make_event(interrupted=True)

    await bridge._pump_events(barge_in_only(), FakeWS([]))
    assert len(speaker.calls) == 1
    assert bridge._buffer_holds_pending_utterance is True

    async def silent_next_turn():
        bridge._utterance.extend(b"\x02\x03" * config.SPEAKER_MIN_UTTERANCE_BYTES)
        yield _make_event(turn_complete=True)

    await bridge._pump_events(silent_next_turn(), FakeWS([]))
    assert len(speaker.calls) == 1, (
        "documented residual changed: the fallback now fires while a pending "
        "utterance is outstanding -- re-read the docstring before accepting this")

    # ...and it settles as soon as any utterance boundary actually arrives.
    async def next_real_boundary():
        yield _make_event(input_transcription=FakeTranscription("sonunda", finished=True))

    await bridge._pump_events(next_real_boundary(), FakeWS([]))
    assert len(speaker.calls) == 2
    assert bridge._buffer_holds_pending_utterance is False


@pytest.mark.asyncio
async def test_a_repeat_barge_in_does_not_truncate_the_live_utterance():
    """The onset trim answers "where did the barge-in start?" -- which is only an
    open question for the FIRST barge-in of a pending utterance.

    On a repeat, the audio in front of the onset window is no longer the model's
    speaking time: it is the user's own speech, already accumulating. Trimming
    again throws it away and scores whatever 0.5 s happens to be at the tail --
    against thresholds calibrated on ~3 s clips.

    Reachable straight from ADK: gemini_llm_connection.py:398 copies
    `interrupted` verbatim onto the combined turn_complete response, so an
    interruption spanning two server messages delivers the flag twice.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))

    model_time_noise = b"\x00" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 3)
    onset = b"\x11" * config.SPEAKER_BARGE_IN_ONSET_BYTES
    kept_speaking = b"\x22" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 2)

    bridge._utterance = bytearray(model_time_noise + onset)

    async def turn():
        yield _make_event(interrupted=True)          # first barge-in: trim is right
        bridge._utterance.extend(kept_speaking)      # the user keeps talking
        yield _make_event(turn_complete=True, interrupted=True)   # ADK repeats the flag

    await bridge._pump_events(turn(), FakeWS([]))

    assert bytes(bridge._utterance) == onset + kept_speaking, (
        f"the repeat barge-in cut the live utterance down to "
        f"{len(bridge._utterance)} bytes; {len(onset + kept_speaking)} had been spoken")

    # ...and it is that whole utterance that gets scored at its own boundary.
    async def boundary():
        yield _make_event(input_transcription=FakeTranscription("devam", finished=True))

    await bridge._pump_events(boundary(), FakeWS([]))
    assert speaker.calls[-1][1] == onset + kept_speaking


@pytest.mark.asyncio
async def test_a_barge_in_keeps_only_the_onset_not_the_model_s_speaking_time():
    """At a barge-in the buffer is [audio collected while the model spoke] +
    [the barge-in speech so far] -- the mic never stopped. Keeping all of it
    means the barge-in utterance is scored with up to a full window of
    non-utterance audio in front of it, and speaker.embed averages the whole
    PCM into one embedding. Keep only the onset window."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"turn-one-audio")

    model_time_noise = b"\x00" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 4)
    onset = b"\x11\x22" * config.SPEAKER_BARGE_IN_ONSET_BYTES

    async def turn():
        yield _make_event(input_transcription=FakeTranscription("bir", finished=True))
        bridge._utterance.extend(model_time_noise + onset)
        yield _make_event(interrupted=True)

    await bridge._pump_events(turn(), FakeWS([]))

    kept = bytes(bridge._utterance)
    assert len(kept) == config.SPEAKER_BARGE_IN_ONSET_BYTES, (
        f"kept {len(kept)} bytes of the {len(model_time_noise + onset)} buffered "
        "at the barge-in -- the model's speaking time is still in front of the "
        "barge-in utterance")
    assert kept == onset[-config.SPEAKER_BARGE_IN_ONSET_BYTES:], (
        "the kept window is not the most recent audio")


@pytest.mark.asyncio
async def test_the_barge_in_onset_window_is_its_own_positive_constant(monkeypatch):
    """The onset window and the fallback's minimum length are two DIFFERENT
    concepts that happen to share a default. Deriving the trim from the floor
    couples them, and the floor is operator-tunable down to 0 -- a documented,
    deliberate opt-out. `del buf[:-0]` is `del buf[:0]`, a NO-OP, so that
    setting would silently retire the trim and put the model's whole speaking
    time back in front of the barge-in utterance.

    This is the same `del buf[:-0]` trap the mic window was already fixed for;
    it came back through the coupling, not through the arithmetic. The onset
    window is therefore its own constant, always positive, and not reachable
    from that env var at all.
    """
    import importlib

    # RELOAD, not monkeypatch: the coupling this guards against happens at
    # IMPORT time, so rebinding the floor on the already-imported module cannot
    # see it. The env var has to be in place before the constants are computed.
    monkeypatch.setenv("JARVIS_SPEAKER_MIN_UTTERANCE_SECONDS", "0")
    try:
        importlib.reload(config)
        assert config.SPEAKER_MIN_UTTERANCE_BYTES == 0, "the opt-out did not take effect"

        # BEHAVIOUR under the hostile env, not just `> 0`. Asserting positivity
        # alone lets `max(SPEAKER_MIN_UTTERANCE_BYTES, 1)` through -- the coupling
        # restored with positivity bolted on, which is the obvious shape of a
        # future "fix" and is WORSE than the no-op it replaces: it would keep a
        # single byte of the utterance instead of the whole buffer.
        speaker = FakeSpeaker((True, 0.9))
        bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                    speaker_service=speaker, presence="locked"))
        buffered = b"\x00" * (config.SPEAKER_BARGE_IN_ONSET_BYTES * 3)
        bridge._utterance = bytearray(buffered)

        async def barge_in():
            yield _make_event(interrupted=True)

        await bridge._pump_events(barge_in(), FakeWS([]))
        kept = len(bridge._utterance)
        assert kept == config.SPEAKER_BARGE_IN_ONSET_BYTES, (
            f"with the floor opted out the trim kept {kept} of {len(buffered)} bytes; "
            f"a usable onset window is {config.SPEAKER_BARGE_IN_ONSET_BYTES}")
        # Half a second of audio, not a token byte.
        assert kept >= int(0.25 * vp.AUDIO_IN_RATE * 2), (
            f"the onset window degraded to {kept} bytes -- technically positive, "
            "practically no audio at all")
    finally:
        monkeypatch.undo()
        importlib.reload(config)


@pytest.mark.asyncio
async def test_a_verified_turn_drains_the_inter_turn_audio():
    """The buffer's lifecycle belongs to the turn, not only to verification.

    When a turn IS verified at its finished transcription, the turn_complete
    fallback is skipped -- and _verify_utterance, the only thing that drains the
    buffer, is never called. Everything the open mic collected while the model
    spoke (up to the whole 10 s window, including the assistant's own TTS echo
    when no headset is used) then survives as a PREFIX of the next turn's
    utterance. speaker.embed averages the entire PCM into one embedding with no
    VAD or trimming, so that prefix drags the next turn's score toward
    different-speaker frames -- and it silently invalidates threshold
    calibration measured on clean clips.
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"turn-one-audio")

    async def turn_one():
        yield _make_event(input_transcription=FakeTranscription("birinci", finished=True))
        bridge._utterance.extend(b"\x00\x01" * config.SPEAKER_MIN_UTTERANCE_BYTES)
        yield _make_event(turn_complete=True)

    await bridge._pump_events(turn_one(), FakeWS([]))
    assert len(speaker.calls) == 1
    assert bytes(bridge._utterance) == b"", (
        "inter-turn audio survived into the next turn's utterance buffer")

    bridge._utterance = bytearray(b"turn-two-audio")

    async def turn_two():
        yield _make_event(input_transcription=FakeTranscription("ikinci", finished=True))

    await bridge._pump_events(turn_two(), FakeWS([]))
    assert speaker.calls[-1][1] == b"turn-two-audio", (
        "the second turn was scored on more than its own utterance")


@pytest.mark.asyncio
async def test_turn_complete_fallback_skips_a_sub_floor_fragment():
    """Defence in depth, independent of the latch.

    The fallback fires when no finished transcription arrived, so unlike the
    transcription path it has NO positive signal that the buffer holds a whole
    utterance. Scoring a fragment against thresholds calibrated on ~3 s clips
    produces an arbitrary verdict -- and an unverified verdict is a LOW under
    locked/ambient, i.e. it actively harms. Below the floor, publish nothing
    and let the per-connection baseline stand (fails safe, never HIGH).
    """
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x00" * (config.SPEAKER_MIN_UTTERANCE_BYTES - 2))

    ws = FakeWS([])

    async def turn_end():
        yield _make_event(turn_complete=True)

    await bridge._pump_events(turn_end(), ws)

    assert speaker.calls == [], "ECAPA ran on a sub-floor fragment"
    assert bytes(bridge._utterance) == b"", "the fragment must still be dropped"
    assert all("speaker" not in t for _, t in ws.sent if isinstance(t, str)), (
        "a skipped verification must not tell the client the speaker was rejected")


@pytest.mark.asyncio
async def test_turn_complete_fallback_verifies_at_the_floor():
    """The floor's inclusive boundary: exactly SPEAKER_MIN_UTTERANCE_BYTES is
    enough audio, so the fallback still does its job for real utterances."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    pcm = b"\x01" * config.SPEAKER_MIN_UTTERANCE_BYTES
    bridge._utterance = bytearray(pcm)

    async def turn_end():
        yield _make_event(turn_complete=True)

    await bridge._pump_events(turn_end(), FakeWS([]))

    assert len(speaker.calls) == 1
    assert speaker.calls[0][1] == pcm


@pytest.mark.asyncio
async def test_finished_transcription_verifies_below_the_floor():
    """The floor is scoped to the FALLBACK path on purpose. A finished input
    transcription is Gemini telling us the buffer holds a complete user
    utterance -- a positive signal the fallback does not have. Applying the
    floor there too would silently stop verifying short commands ("evet",
    "kapat"), which is the opposite of what this slice is for."""
    speaker = FakeSpeaker((True, 0.9))
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=speaker, presence="locked"))
    bridge._utterance = bytearray(b"\x02\x03")

    async def finished():
        yield _make_event(input_transcription=FakeTranscription("evet", finished=True))

    await bridge._pump_events(finished(), FakeWS([]))
    assert len(speaker.calls) == 1


@pytest.mark.asyncio
async def test_the_mic_pump_never_touches_the_per_turn_latch():
    """The latch belongs to the turn lifecycle (_pump_events), never to the
    byte stream.

    Buffer emptiness is NOT a proxy for "a new turn started": _verify_utterance
    drains that same buffer mid-turn, so an empty buffer equally means "the
    utterance was just scored and the model is about to speak". Keying a
    re-arm on it made every turn verify twice -- see
    test_post_utterance_mic_frames_do_not_cause_a_second_verification. BOTH
    buffer states are pinned here so that mechanism cannot come back."""
    speaker = FakeSpeaker((True, 0.9))

    for label, buffered in (("empty (just drained)", b""),
                            ("mid-utterance", b"already-buffered")):
        bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                    speaker_service=speaker, presence="locked"))
        bridge._verified_this_turn = True
        bridge._utterance = bytearray(buffered)

        ws = FakeWS([{"type": "websocket.receive", "bytes": b"more"}])
        await bridge._pump_mic_once(ws, FakeQueue())

        assert bridge._verified_this_turn is True, f"the mic pump re-armed the latch ({label})"
        assert bytes(bridge._utterance) == buffered + b"more"


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


def test_the_fallback_floor_is_validated_at_both_ends(caplog):
    """The floor's sibling is validated at load; this knob needs it for the same
    reason, and its two failure directions are NOT symmetric.

    Negative: a floor no buffer can be below, i.e. the fragment guard silently
    gone -- fails CLOSED-ish (back to scoring fragments, which reject).
    Above the mic window: a floor no buffer can ever REACH, so the turn_complete
    fallback never fires again and every turn without a finished transcription
    passes UNVERIFIED -- that one fails OPEN, so a typo must not reach it.
    """
    with caplog.at_level("WARNING"):
        assert (config._effective_min_utterance_seconds(-5.0)
                == config._DEFAULT_MIN_UTTERANCE_SECONDS)
    assert "removes the fallback's fragment guard" in caplog.text
    caplog.clear()

    with caplog.at_level("WARNING"):
        too_long = config.SPEAKER_UTTERANCE_SECONDS + 1.0
        assert (config._effective_min_utterance_seconds(too_long)
                == config.SPEAKER_UTTERANCE_SECONDS)
    assert "could never fire" in caplog.text

    # Usable values pass through, including an explicit 0 -- that is a
    # deliberate opt-out of the extra guard, not a silent misconfiguration.
    assert config._effective_min_utterance_seconds(0.75) == 0.75
    assert config._effective_min_utterance_seconds(0.0) == 0.0


def test_the_shipped_floor_constant_actually_went_through_that_validation(monkeypatch):
    """Calling the validator directly proves the validator; it does NOT prove
    the module USES it. Wiring the guard to the shipped constant is a separate
    claim and gets its own test -- twice on this branch a guard was asserted one
    function short of the code that runs in production.

    Reload the module under a hostile environment and read the constant."""
    import importlib

    monkeypatch.setenv("JARVIS_SPEAKER_MIN_UTTERANCE_SECONDS",
                       str(config.SPEAKER_UTTERANCE_SECONDS + 60))
    try:
        importlib.reload(config)
        assert config.SPEAKER_MIN_UTTERANCE_BYTES == config.SPEAKER_UTTERANCE_MAX_BYTES, (
            "an out-of-range floor reached SPEAKER_MIN_UTTERANCE_BYTES unclamped: "
            "the turn_complete fallback could never fire and every turn without a "
            "finished transcription would pass unverified")
    finally:
        monkeypatch.undo()
        importlib.reload(config)

    assert 0 <= config.SPEAKER_MIN_UTTERANCE_BYTES <= config.SPEAKER_UTTERANCE_MAX_BYTES
