import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

import app.voice as voice_mod
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
    email = await _handshake(ws)
    assert email == "user@example.com"
    assert ws.sent == []
    assert ws.closed_with is None


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
