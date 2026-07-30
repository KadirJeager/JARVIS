"""End-to-end proof for Katman 2b Dilim 3a (Kadir ses-kimligi), protocol v2:
drives the REAL /ws/voice endpoint (FastAPI TestClient websocket) through the
REAL VoiceBridge -- the receive loop (run()'s while loop), the inline
verification at the user_text boundary, and the spawned turn task -- with only
the ADK runner, session service, and speaker service faked. Proves
hello(caps/device/presence) -> mic PCM -> user_text -> evt_speaker AND that
identity verification tightens the published trust level for a locked+match
utterance (spec's risk-based trust fusion, spec section 6/11).

v2 note: the hello MUST carry client_caps -- a v1 (caps-less) hello is
rejected with 4409 by the real handshake.
"""
import contextlib
import json
import queue
import threading

import pytest
from fastapi.testclient import TestClient

from app.speaker_store import enroll_anchors
from tests.fakes import FakeDB

_HELLO = json.dumps({
    "token": "t", "device_hint": "tablet", "presence": "locked",
    "client_caps": {"stt": "device", "tts": "device", "proto": 2},
})
_USER_TEXT = json.dumps(
    {"type": "user_text", "text": "merhaba", "utterance_final": True})


class _FinalResponse:
    """Mirrors the ADK Event surface the bridge's turn reads (is_final_response
    + content.parts[0].text)."""

    def __init__(self, text):
        from google.genai import types

        self.content = types.Content(role="model", parts=[types.Part(text=text)])

    def is_final_response(self):
        return True


class _OneTurnRunner:
    """Fake ADK text runner: answers every run_async turn with one
    final-response event. Deterministic by construction -- no timing hacks:
    the v2 bridge verifies the buffered PCM INLINE at the user_text frame
    (same receive loop, same ordering as the bytes that fed the buffer), so
    unlike the v1 live-events fake this one cannot race the mic path."""

    def run_async(self, **kwargs):
        async def events():
            yield _FinalResponse("selam")

        return events()


class _Sessions:
    def __init__(self):
        self._s = type("S", (), {"state": {}})()

    async def get_session(self, **k):
        return self._s

    async def create_session(self, **k):
        return self._s


def _receive_with_timeout(ws, timeout=5.0):
    """starlette's WebSocketTestSession.receive() has no built-in timeout -- if
    the server-side ordering is wrong and no more messages are ever sent, this
    call blocks forever. A daemon thread + bounded queue.get turns that failure
    mode into a clean pytest FAILURE instead of a hung test run."""
    q: "queue.Queue" = queue.Queue(maxsize=1)

    def _target():
        # If we time out below, this thread is abandoned (still blocked in
        # ws.receive()) until the caller's `with websocket_connect(...)` block
        # eventually tears the socket down -- that teardown unblocks it via an
        # EndOfStream. Swallow it: the thread has nowhere useful to report to
        # by then, and an unhandled-thread-exception warning would just be
        # noise on top of the real (already-reported) timeout failure.
        with contextlib.suppress(Exception):
            q.put(ws.receive())

    threading.Thread(target=_target, daemon=True).start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(f"ws.receive() did not return within {timeout}s (ordering regression?)")


@pytest.fixture
def wired(monkeypatch):
    import app.main as main

    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [[1.0, 0.0, 0.0]])
    sessions = _Sessions()
    monkeypatch.setattr(main, "get_voice_runner_sessions_memory",
                         lambda: (_OneTurnRunner(), sessions, None))
    # speaker service with a fake embed: any pcm -> matching vector
    from app.speaker import SpeakerService
    svc = SpeakerService(db, embed_fn=lambda pcm: [1.0, 0.0, 0.0], now_fn=lambda: "t",
                          accept=0.35, adapt=0.6, cap=20, top_k=3)
    monkeypatch.setattr(main, "get_speaker_service", lambda: svc)
    monkeypatch.setattr("app.voice.verify_token_email", lambda t: "kadir@example.com")
    return main, sessions


def test_ws_voice_emits_speaker_event_and_publishes_locked_trust(wired, monkeypatch):
    main, sessions = wired
    from app import trust, voice, voice_trust

    published = []
    original_publish = voice_trust.publish
    monkeypatch.setattr(voice_trust, "publish", lambda key, signals: (
        published.append((key, signals)), original_publish(key, signals)))

    client = TestClient(main.app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(_HELLO)
        ws.send_bytes(b"\x00\x01\x00\x01")            # mic audio -> buffered
        ws.send_text(_USER_TEXT)                      # the v2 utterance boundary
        # drain events until we have both the speaker event and the reply
        seen = {}
        for _ in range(6):
            msg = _receive_with_timeout(ws)
            if "text" in msg:
                event = json.loads(msg["text"])
                seen.setdefault(event["type"], event)
            if "turn_complete" in seen:
                break
        assert seen["speaker"] == {
            "type": "speaker", "role": "user", "verified": True, "score": 1.0}
        assert seen["jarvis_text"] == {"type": "jarvis_text", "text": "selam"}
        assert "turn_complete" in seen

    key = voice_trust.key_for(voice.APP_NAME, "kadir@example.com", "voice-kadir@example.com")
    # locked + match -> MEDIUM, published under this connection's session key
    # for the policy layer (voice_trust.py); the initial no-voice-evidence
    # publish at connection start is the first entry.
    assert [(k, s.trust_level) for k, s in published] == [
        (key, trust.MEDIUM), (key, trust.MEDIUM)]
    assert published[-1][1].voice_score == 1.0
    assert published[-1][1].device_hint == "tablet"
    assert voice_trust.peek(key) is None               # cleared at teardown


def test_ws_voice_rejects_a_v1_client_with_4409(wired):
    """End-to-end through the REAL handshake: a caps-less hello (a client
    still expecting the retired Gemini Live bridge) gets the upgrade error and
    the distinct close code -- after authenticating fine."""
    main, sessions = wired

    client = TestClient(main.app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(json.dumps({"token": "t"}))       # no client_caps
        msg = _receive_with_timeout(ws)
        assert json.loads(msg["text"]) == {"type": "error", "message": "Uygulamayı güncelle"}
        # After the error frame the server closes with 4409; starlette surfaces
        # the close on the next receive.
        msg = _receive_with_timeout(ws)
        assert msg["type"] == "websocket.close"
        assert msg["code"] == 4409


def test_e2e_verified_utterance_lands_in_the_verification_history(wired):
    """Wiring proof at the outermost seam: after the WS-driven utterance the
    history doc must hold the fused presence/trust_level -- the fields only
    voice.py knows -- so bridge -> service -> store is pinned end to end."""
    main, sessions = wired
    from app import speaker_history, trust

    client = TestClient(main.app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(_HELLO)
        ws.send_bytes(b"\x00\x01\x00\x01")            # mic audio -> buffered
        ws.send_text(_USER_TEXT)
        for _ in range(6):
            msg = _receive_with_timeout(ws)
            if "text" in msg and '"speaker"' in msg["text"]:
                break

    svc = main.get_speaker_service()
    entries = speaker_history.load_history(svc.db, "kadir@example.com")
    assert len(entries) == 1
    assert entries[0]["verified"] is True
    assert entries[0]["presence"] == "locked"
    assert entries[0]["trust_level"] == trust.MEDIUM
    assert entries[0]["vec"] == [1.0, 0.0, 0.0]
