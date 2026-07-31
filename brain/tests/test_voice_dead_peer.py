"""Dead-peer tests for app/voice.py's _safe_send guard.

Live incident (2026-07-31, jarvis-voice logs): a client that hung up mid-turn
left the turn task sending jarvis_text/turn_complete into a closed socket,
and uvicorn answered with `RuntimeError: Unexpected ASGI message
'websocket.send', after sending 'websocket.close'` — a full ERROR traceback
for what is ordinary client behaviour. These tests pin that a dead peer is
treated as "client gone", never as a server error.
"""
import asyncio
import json

import pytest

from app import voice_protocol as vp
from app.voice import VoiceBridge

from tests.test_voice import (  # noqa: E402  (same-directory test helpers)
    FakeTextRunner, FakeWS, _armed, _drive_turns, _sent_json, _user_text,
)


class DeadAfterWS(FakeWS):
    """FakeWS whose send_text starts raising send-after-close RuntimeError
    once `die()` is called -- the exact failure uvicorn produces."""

    def __init__(self, incoming):
        super().__init__(incoming)
        self._dead = False

    def die(self):
        self._dead = True

    async def send_text(self, t):
        if self._dead:
            raise RuntimeError(
                "Unexpected ASGI message 'websocket.send', after sending "
                "'websocket.close'."
            )
        await super().send_text(t)


@pytest.mark.asyncio
async def test_turn_answer_into_dead_socket_is_swallowed_not_an_error():
    """Client hangs up while the model is thinking: the turn finishes into
    _safe_send, which marks the bridge closed and sends nothing -- no
    exception, and the transcript still records the reply for the snapshot."""
    runner = FakeTextRunner("selam")
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    ws = DeadAfterWS([])
    ws.die()  # peer already gone before the answer arrives
    await bridge._handle_text_frame(ws, _user_text("merhaba"))
    await _drive_turns(bridge)   # must not raise

    assert bridge._closed is True
    assert ws.sent == []         # nothing was attempted after the close
    # The reply is still in the transcript: the snapshot is the dead client's
    # only way to see it on reconnect.
    assert bridge.transcript[-1] == {"role": "jarvis", "text": "selam"}


@pytest.mark.asyncio
async def test_sends_after_closed_are_no_ops_even_without_prior_failure():
    """Once _closed is set (e.g. run()'s finally after a disconnect), later
    sends short-circuit WITHOUT touching the socket at all."""
    runner = FakeTextRunner("selam")
    bridge = _armed(VoiceBridge(runner=runner, session_service=None))
    bridge._closed = True
    ws = FakeWS([])
    assert await bridge._safe_send(ws, vp.evt_turn_complete()) is False
    assert ws.sent == []


@pytest.mark.asyncio
async def test_safe_send_reports_success_on_a_live_socket():
    bridge = _armed(VoiceBridge(runner=FakeTextRunner(), session_service=None))
    ws = FakeWS([])
    assert await bridge._safe_send(ws, vp.evt_turn_complete()) is True
    assert bridge._closed is False
    assert _sent_json(ws) == [{"type": "turn_complete"}]
