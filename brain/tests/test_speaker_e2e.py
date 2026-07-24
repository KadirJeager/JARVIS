"""End-to-end proof for Katman 2b Dilim 3a (Kadir ses-kimligi): drives the REAL
/ws/voice endpoint (FastAPI TestClient websocket) through the REAL VoiceBridge
concurrency -- the concurrently running mic-pump loop (run()'s while loop) and
_pump_events task -- with only the ADK runner, session service, and speaker
service faked. Proves hello(device/presence) -> mic PCM -> evt_speaker AND that
identity verification tightens session.state's trust level for a locked+match
utterance (spec's risk-based trust fusion, spec section 6/11).

This test also answers spec section 15's open "run_live ordering" question --
see task-11-report.md for the full writeup of what the real event ordering
turned out to be under TestClient's thread+event-loop scheduling."""
import asyncio
import contextlib
import json
import queue
import threading

import pytest
from fastapi.testclient import TestClient

from app.speaker_store import enroll_anchors
from tests.fakes import FakeDB


class _OneShotRunner:
    """Fake ADK live runner: yields exactly one input_transcription event, after
    a short real delay.

    The delay is NOT a test-timing hack layered on top of an otherwise-correct
    zero-latency fake -- it is what makes this fake REALISTIC. A real live
    Gemini transcription always arrives after a network round-trip, so by the
    time it lands, the mic-pump loop has already had time to receive and
    buffer the PCM for that utterance. A zero-delay fake claims a transcript
    exists before the audio that produced it has even been read off the
    socket, which cannot happen for real -- and, proven empirically (see
    task-11-report.md's RED-phase run), races ahead of VoiceBridge.run()'s
    concurrent mic-pump task under TestClient's thread+event-loop scheduling:
    asyncio.create_task(self._pump_events(...)) is scheduled but not yet
    running when run() reaches its own first checkpoint (awaiting the client's
    next message), and a zero-await fake generator lets _pump_events reach
    _verify_utterance before the mic loop's task gets a turn to buffer
    anything -- _verify_utterance then sees an empty buffer, returns early
    without sending evt_speaker, and the client's receive() loop blocks
    forever waiting for an event that will never come."""

    def run_live(self, **kwargs):
        async def events():
            # TODO(debt): wall-clock sleep standing in for the network
            # round-trip a real Gemini live transcript always has (see the
            # class docstring above for why a zero-delay fake races
            # VoiceBridge.run()'s concurrent mic-pump task and breaks this
            # test). 0.05s has been reliable so far but is a wall-clock
            # assumption, not a guarantee -- raise it if this ever flakes
            # under CI load.
            await asyncio.sleep(0.05)

            class T:
                text = "merhaba"

            class E:
                content = None
                turn_complete = False
                input_transcription = T()
                output_transcription = None

            yield E()

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
    mode into a clean pytest FAILURE instead of a hung test run (verified this
    matters: see task-11-report.md's RED-phase evidence, where exactly this
    happened)."""
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
                         lambda: (_OneShotRunner(), sessions, None))
    # speaker service with a fake embed: any pcm -> matching vector
    from app.speaker import SpeakerService
    svc = SpeakerService(db, embed_fn=lambda pcm: [1.0, 0.0, 0.0], now_fn=lambda: "t",
                          accept=0.35, adapt=0.6, cap=20, top_k=3)
    monkeypatch.setattr(main, "get_speaker_service", lambda: svc)
    monkeypatch.setattr("app.voice.verify_token_email", lambda t: "kadir@example.com")
    return main, sessions


def test_ws_voice_emits_speaker_event_and_sets_locked_trust(wired):
    main, sessions = wired
    client = TestClient(main.app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(json.dumps({"token": "t", "device_hint": "tablet", "presence": "locked"}))
        ws.send_bytes(b"\x00\x01\x00\x01")            # mic audio -> buffered
        # drain events until we see the speaker event
        seen = None
        for _ in range(5):
            msg = _receive_with_timeout(ws)
            if "text" in msg and '"speaker"' in msg["text"]:
                seen = json.loads(msg["text"])
                break
        assert seen == {"type": "speaker", "role": "user", "verified": True, "score": 1.0}
    from app import config, trust
    assert sessions._s.state[config.TRUST_STATE_KEY] == trust.MEDIUM   # locked+match -> MEDIUM
