import asyncio
import json

import pytest

from app.voice import VoiceBridge


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
