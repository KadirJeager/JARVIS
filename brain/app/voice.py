"""Voice gateway: bridges a WebSocket to an ADK live session (North Star §4.2).

Contract: see voice_protocol.py (frozen). Same policy/audit/tools/memory as text
chat, but a DEDICATED live runner: the bridge is handed a runner built on
config.resolve_live_model() via main.get_voice_runner_sessions_memory(), sharing
the text path's session_service and memory instances (see main._init_voice for
why the live model differs — an ADK 1.36.2 tool-call deadlock on non-3.x live
models — and app/live_model.py for how it's auto-resolved to the newest usable
one).

ADK's run_live is EXPERIMENTAL (google-adk 1.36.2). Field names below were
verified by reading the installed source (see task-2a2-report.md):
  - google/adk/agents/live_request_queue.py: LiveRequestQueue.send_realtime(blob),
    .close() (no args)
  - google/adk/agents/run_config.py: RunConfig.response_modalities: list[str],
    RunConfig.output_audio_transcription / input_audio_transcription:
    Optional[types.AudioTranscriptionConfig] (default_factory'li, yani
    varsayılan olarak zaten açık)
  - google/adk/runners.py: Runner.run_live(*, user_id, session_id,
    live_request_queue, run_config=None, session=None) -> AsyncGenerator[Event]
  - google/adk/models/llm_response.py (Event extends LlmResponse):
    turn_complete: Optional[bool], input_transcription / output_transcription:
    Optional[types.Transcription]
  - google/genai/types.py: Blob(data=bytes, mime_type=str);
    Transcription(text=Optional[str], ...); Part.inline_data: Optional[Blob]
"""
import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai import types

from . import voice_protocol as vp
from .auth import verify_token_email

router = APIRouter()


class VoiceBridge:
    def __init__(self, runner, session_service, memory=None):
        self.runner = runner
        self.session_service = session_service
        self.memory = memory
        self.transcript: list[dict] = []

    async def _pump_mic_once(self, ws, queue) -> bool:
        """Read one client message and forward mic audio to the live queue.

        Returns False when the client disconnected (caller should stop looping).
        """
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            queue.close()
            return False
        if data := msg.get("bytes"):
            queue.send_realtime(types.Blob(data=data, mime_type=vp.AUDIO_MIME_IN))
        return True

    async def _pump_events(self, events, ws) -> None:
        """Forward ADK live events to the client: audio bytes, turn_complete,
        and transcript events. Field names verified against installed ADK
        source (see module docstring)."""
        async for event in events:
            if getattr(event, "turn_complete", False):
                await ws.send_text(json.dumps(vp.evt_turn_complete()))
            for tr_attr, role in (("input_transcription", "user"), ("output_transcription", "jarvis")):
                tr = getattr(event, tr_attr, None)
                if tr and getattr(tr, "text", None):
                    await ws.send_text(json.dumps(vp.evt_transcript(role, tr.text)))
                    self.transcript.append({"role": role, "text": tr.text})
            content = getattr(event, "content", None)
            for part in (getattr(content, "parts", None) or []):
                blob = getattr(part, "inline_data", None)
                if blob and blob.data:
                    await ws.send_bytes(blob.data)

    async def run(self, ws: WebSocket, user_id: str) -> None:
        session_id = f"voice-{user_id}"
        session = await self.session_service.get_session(
            app_name="jarvis", user_id=user_id, session_id=session_id
        )
        if session is None:
            await self.session_service.create_session(
                app_name="jarvis", user_id=user_id, session_id=session_id
            )
        queue = LiveRequestQueue()
        run_config = RunConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )
        events = self.runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=queue,
            run_config=run_config,
        )
        pump_out = asyncio.create_task(self._pump_events(events, ws))
        try:
            try:
                # LiveRequestQueue.close() (verified in live_request_queue.py) just
                # enqueues a close sentinel on an unbounded asyncio.Queue -- it does
                # NOT reject further send_realtime() puts. So a dead _pump_events
                # task cannot be detected via "send raises"; instead re-check
                # pump_out.done() every iteration so the mic loop stops pumping
                # into a session whose event stream already ended/failed.
                while not pump_out.done() and await self._pump_mic_once(ws, queue):
                    pass
            finally:
                pump_out.cancel()
                try:
                    # If pump_out died with its OWN exception (not cancellation),
                    # awaiting it re-raises that exception here — the nested
                    # finally guarantees events.aclose() still runs, so the live
                    # Gemini session is torn down on every exit path.
                    with contextlib.suppress(asyncio.CancelledError):
                        await pump_out
                finally:
                    with contextlib.suppress(asyncio.CancelledError):
                        await events.aclose()
        finally:
            # Outermost teardown: persist the transcript snapshot on every exit
            # path (happy path, client disconnect, or exception propagating out
            # of the block above). Wrapped in try/except so a Firestore hiccup
            # can never mask the original exception.
            if self.memory is not None and self.transcript:
                try:
                    self.memory.snapshot_session(
                        session_id, user_id, {"transcript": self.transcript[-50:]}
                    )
                except Exception:
                    logging.exception(
                        "voice bridge: failed to snapshot transcript for %s", user_id
                    )


async def _handshake(ws: WebSocket) -> str | None:
    """Read + verify the hello frame. Returns the verified email, or None if
    the handshake did not complete (bad hello -> evt_error + close(4401)
    already sent; client disconnect -> nothing sent, the peer is gone)."""
    try:
        hello = await ws.receive_text()
    except WebSocketDisconnect:
        return None
    try:
        return verify_token_email(vp.parse_hello(hello))
    except (ValueError, PermissionError):
        await ws.send_text(json.dumps(vp.evt_error("Giriş doğrulanamadı")))
        await ws.close(code=4401)
        return None


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    await ws.accept()
    email = await _handshake(ws)
    if email is None:
        return
    from . import main

    try:
        # Runner acquisition is inside the try too: a cold-start infra hiccup
        # (Firestore client init, live-model resolution) must close the socket
        # gracefully with an error frame, not propagate unhandled through
        # Starlette (which has no websocket exception handler) -- matching how
        # the text path wraps its _init() inside run_turn's caller.
        runner, sessions, memory = main.get_voice_runner_sessions_memory()
        await VoiceBridge(runner, sessions, memory=memory).run(ws, user_id=email)
    except Exception:
        logging.exception("voice bridge failed for %s", email)
        await ws.send_text(json.dumps(vp.evt_error("Sesli oturum düştü, tekrar bağlan")))
        await ws.close(code=1011)
