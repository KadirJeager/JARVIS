import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

import app.voice as voice_mod
from app import config, trust
from app.voice import VoiceBridge, _handshake


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
    """Mirrors google.genai.types.Transcription (text: Optional[str])."""

    def __init__(self, text):
        self.text = text


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
async def test_bridge_verifies_utterance_and_writes_trust_and_event():
    session = type("S", (), {"state": {}})()
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(data=b"\x00\x01")                       # mic-ish (ignored here)
        yield _make_event(input_transcription=FakeTranscription("merhaba"))

    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None, speaker_service=speaker,
                         device_hint="headset", presence="locked", session=session)
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")            # buffered mic audio
    await bridge._pump_events(fake_events(), ws)

    assert speaker.calls and speaker.calls[0][2] == "headset"     # identify called w/ device
    # locked + verified -> MEDIUM in session.state
    assert session.state[config.TRUST_STATE_KEY] == trust.MEDIUM
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
    session = type("S", (), {"state": {}})()

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba"))

    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None, speaker_service=ExplodingSpeaker(),
                         device_hint="phone", presence="locked", session=session)
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")             # buffered mic audio

    with caplog.at_level("ERROR"):
        await bridge._pump_events(fake_events(), ws)                # must not raise

    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": False, "score": 0.0})) in ws.sent
    assert session.state[config.TRUST_STATE_KEY] == trust.LOW
    assert "speaker.identify failed" in caplog.text


@pytest.mark.asyncio
async def test_run_resets_stale_trust_for_new_connection():
    """A new voice connection must NOT inherit the trust level a previous
    connection left in the process-lifetime session state."""
    session = type("S", (), {"state": {config.TRUST_STATE_KEY: trust.HIGH}})()  # stale HIGH

    class Sessions:
        async def get_session(self, **k):
            return session

        async def create_session(self, **k):
            return session

    class NoEvents:
        def run_live(self, **kwargs):
            async def events():
                return
                yield  # pragma: no cover

            return events()

    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(
        runner=NoEvents(), session_service=Sessions(),
        speaker_service=FakeSpeaker((True, 0.9)),
        presence="locked", device_hint="tablet",
    )
    await asyncio.wait_for(bridge.run(ws, user_id="kadir@example.com"), timeout=2)
    # locked + no voice evidence yet -> MEDIUM, NOT the stale HIGH
    assert session.state[config.TRUST_STATE_KEY] == trust.MEDIUM


@pytest.mark.asyncio
async def test_run_does_not_touch_session_state_without_speaker_service():
    """No speaker_service -> identity is off -> run() must not write trust
    (the existing FakeSessionService returns a bare object() with no .state)."""

    class Sessions:
        async def get_session(self, **k):
            return object()

        async def create_session(self, **k):
            return object()

    class NoEvents:
        def run_live(self, **kwargs):
            async def events():
                return
                yield  # pragma: no cover

            return events()

    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(runner=NoEvents(), session_service=Sessions())  # no speaker_service
    await asyncio.wait_for(bridge.run(ws, user_id="kadir@example.com"), timeout=2)  # must not raise


@pytest.mark.asyncio
async def test_run_with_speaker_service_and_stateless_session_does_not_raise():
    """The test above proves the trust-init block is skipped when
    speaker_service is None -- it never actually forces execution INTO the
    `getattr(self.session, "state", None)` guard. Here speaker_service IS set
    (forcing entry into that branch) while the session still has no `.state`
    attribute at all: a bare `self.session.state[...] = level` would raise
    AttributeError, so this is the combination that makes the getattr guard
    load-bearing rather than dead defensive code."""

    class Sessions:
        async def get_session(self, **k):
            return object()          # no .state attribute

        async def create_session(self, **k):
            return object()

    class NoEvents:
        def run_live(self, **kwargs):
            async def events():
                return
                yield  # pragma: no cover

            return events()

    ws = YieldingFakeWS([{"type": "websocket.disconnect"}])
    bridge = VoiceBridge(
        runner=NoEvents(), session_service=Sessions(),
        speaker_service=FakeSpeaker((True, 0.9)),
        presence="locked", device_hint="phone",
    )
    await asyncio.wait_for(bridge.run(ws, user_id="kadir@example.com"), timeout=2)  # must not raise
