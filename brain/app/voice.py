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
    Transcription(text=Optional[str], finished=Optional[bool], ...);
    Part.inline_data: Optional[Blob]

Speaker identity (Katman 2b Dilim 3a): mic PCM is buffered in a BOUNDED rolling
window, verified ONCE per turn at the utterance boundary (input_transcription
with finished=True, or turn_complete as the fallback ADK's own flush implies --
models/gemini_llm_connection.py:283-323 and :349-367), off the event loop via
asyncio.to_thread, and the resulting trust level is published through
app/voice_trust.py. It is NOT written into session.state: ADK 1.36.2 hands the
bridge and the runner independent session copies, so such a write never reaches
the policy callback -- see voice_trust.py for the source-verified detail.
"""
import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai import types

from . import config, trust, voice_trust
from . import voice_protocol as vp
from .auth import verify_token_email

router = APIRouter()

APP_NAME = "jarvis"   # must match main.APP_NAME: it is part of the ADK session identity


class VoiceBridge:
    def __init__(self, runner, session_service, memory=None, speaker_service=None,
                 device_hint="unknown", presence="foreground"):
        self.runner = runner
        self.session_service = session_service
        self.memory = memory
        self.speaker_service = speaker_service
        self.device_hint = device_hint
        self.presence = presence
        self.transcript: list[dict] = []
        self._utterance = bytearray()   # accumulates this turn's mic PCM
        self._user_id = ""
        self._trust_key: voice_trust.SessionKey | None = None
        self._verified_this_turn = False

    def _publish_trust(self, level: str, voice_score: float | None) -> None:
        """Make this connection's identity signals visible to the policy layer.
        No-op before run() has established the session key. See voice_trust.py
        for why this cannot just be a session.state write."""
        if self._trust_key is None:
            return
        voice_trust.publish(self._trust_key, voice_trust.VoiceSignals(
            trust_level=level, voice_score=voice_score,
            presence=self.presence, device_hint=self.device_hint,
        ))

    async def _pump_mic_once(self, ws, queue) -> bool:
        """Read one client message and forward mic audio to the live queue.

        Returns False when the client disconnected (caller should stop looping).
        """
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            queue.close()
            return False
        if data := msg.get("bytes"):
            if self.speaker_service is not None:
                self._utterance.extend(data)
                # BOUNDED buffer: the drain only happens at a turn boundary, and
                # a turn boundary is not guaranteed to arrive (long silence, a
                # model hiccup, transcription misconfigured). Unbounded, this
                # grows at AUDIO_IN_RATE*2 = 32 KB/s (~115 MB/h) inside a
                # process that already carries torch. Keeping only the most
                # recent window also bounds inference time, and a window that
                # long is far more audio than ECAPA needs.
                if len(self._utterance) > config.SPEAKER_UTTERANCE_MAX_BYTES:
                    del self._utterance[:-config.SPEAKER_UTTERANCE_MAX_BYTES]
            queue.send_realtime(types.Blob(data=data, mime_type=vp.AUDIO_MIME_IN))
        return True

    async def _verify_utterance(self, ws) -> None:
        """Called at the user utterance boundary: run speaker identity on the
        buffered PCM, fuse into a trust level, publish it for the policy layer,
        and tell the client. Any failure is logged and treated as unverified --
        it must never break the audio stream."""
        pcm = bytes(self._utterance)
        self._utterance.clear()
        if not pcm:
            return
        try:
            # OFF THE EVENT LOOP: real ECAPA inference (plus the ~89 MB lazy
            # model load on the very first call) is seconds of blocking CPU. On
            # the loop it would stall this session's audio, its mic pump, and
            # every other WS connection this instance serves. speaker._get_model
            # is lock-guarded precisely because this makes concurrent first
            # calls possible.
            verified, score = await asyncio.to_thread(
                self.speaker_service.identify,
                self._user_id, pcm, self.device_hint, auth_is_kadir=True,
            )
        except Exception:
            logging.exception("voice bridge: speaker.identify failed for %s", self._user_id)
            verified, score = False, 0.0
        level = trust.assess(
            trust.TrustContext(
                auth_verified=True, presence=self.presence,
                # Floor an unverified score to "no positive evidence" so a
                # near-miss can never fuse as a match even if the service's
                # accept threshold were ever configured apart from config's.
                voice_score=score if verified else 0.0, device_hint=self.device_hint,
            ),
            config.SPEAKER_ACCEPT_THRESHOLD,
        )
        # voice_score in the audit is the MEASURED voiceprint match (spec §6's
        # definition), not the floored fusion input -- "how close was it" is
        # what makes a past decision reconstructable.
        self._publish_trust(level, score)
        await ws.send_text(json.dumps(vp.evt_speaker("user", verified, score)))
        logging.info(
            "voice trust: user=%s verified=%s score=%.4f presence=%s device=%s level=%s",
            self._user_id, verified, score, self.presence, self.device_hint, level,
        )

    async def _pump_events(self, events, ws) -> None:
        """Forward ADK live events to the client: audio bytes, turn_complete,
        and transcript events. Field names verified against installed ADK
        source (see module docstring)."""
        async for event in events:
            if getattr(event, "turn_complete", False):
                await ws.send_text(json.dumps(vp.evt_turn_complete()))
                # Fallback: the model never sent a finished input transcription
                # for this turn (ADK itself flushes pending transcriptions on
                # turn_complete/generation_complete/interrupted precisely
                # because "the Gemini API or Vertex AI might not send a
                # transcription finished signal" --
                # models/gemini_llm_connection.py:349-367). Verify what we
                # buffered rather than silently skipping the turn.
                if self.speaker_service is not None and not self._verified_this_turn:
                    await self._verify_utterance(ws)
                self._verified_this_turn = False
            for tr_attr, role in (("input_transcription", "user"), ("output_transcription", "jarvis")):
                tr = getattr(event, tr_attr, None)
                if tr and getattr(tr, "text", None):
                    await ws.send_text(json.dumps(vp.evt_transcript(role, tr.text)))
                    self.transcript.append({"role": role, "text": tr.text})
                    # ONCE PER TURN, at the utterance boundary -- never on a
                    # partial fragment. Verified in the installed ADK source
                    # (models/gemini_llm_connection.py:283-323): incremental
                    # input transcriptions are yielded with finished=False /
                    # partial=True, and the whole-utterance one with
                    # finished=True / partial=False (Gemini 3.x Live, which
                    # config.resolve_live_model() selects, sends only the
                    # latter). Embedding a fragment would score arbitrary
                    # sub-second audio against thresholds calibrated on ~3 s
                    # clips.
                    if (role == "user" and self.speaker_service is not None
                            and getattr(tr, "finished", False)
                            and not self._verified_this_turn):
                        self._verified_this_turn = True
                        await self._verify_utterance(ws)
            content = getattr(event, "content", None)
            for part in (getattr(content, "parts", None) or []):
                blob = getattr(part, "inline_data", None)
                if blob and blob.data:
                    await ws.send_bytes(blob.data)

    async def run(self, ws: WebSocket, user_id: str) -> None:
        session_id = f"voice-{user_id}"
        # The session must EXIST before run_live: Runner.auto_create_session
        # defaults to False (runners.py:142), so the runner's own
        # _get_or_create_session would raise SessionNotFoundError otherwise.
        # The returned object is only ADK's copy -- never a channel back to the
        # runner (voice_trust.py) -- so nothing is kept from it.
        session = await self.session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if session is None:
            await self.session_service.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )
        self._trust_key = voice_trust.key_for(APP_NAME, user_id, session_id)
        if self.speaker_service is not None:
            # Initialize THIS connection's trust from its own context: a new
            # connection must never inherit a previous one's level, and any
            # tool call that lands before the first utterance is verified (ADK
            # does not guarantee transcription arrives before tool_call --
            # models/gemini_llm_connection.py:280-282) must already see the
            # tightened, no-voice-evidence-yet level rather than the HIGH
            # default.
            self._publish_trust(
                trust.assess(
                    trust.TrustContext(
                        auth_verified=True, presence=self.presence,
                        voice_score=None,   # no voice evidence yet this connection
                        device_hint=self.device_hint,
                    ),
                    config.SPEAKER_ACCEPT_THRESHOLD,
                ),
                voice_score=None,
            )
        self._user_id = user_id
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
            # Outermost teardown. Drop this connection's trust FIRST: the socket
            # is gone, so its identity evidence is stale, and a leftover entry
            # would be visible to any later tool call on the same ADK session
            # id (which is per-USER and outlives the connection).
            if self._trust_key is not None:
                voice_trust.clear(self._trust_key)
            # Persist the transcript snapshot on every exit path (happy path,
            # client disconnect, or exception propagating out of the block
            # above). Wrapped in try/except so a Firestore hiccup can never mask
            # the original exception.
            if self.memory is not None and self.transcript:
                try:
                    self.memory.snapshot_session(
                        session_id, user_id, {"transcript": self.transcript[-50:]}
                    )
                except Exception:
                    logging.exception(
                        "voice bridge: failed to snapshot transcript for %s", user_id
                    )


async def _handshake(ws: WebSocket) -> tuple[str, str, str] | None:
    """Read + verify the hello frame. Returns (email, device_hint, presence),
    or None if the handshake did not complete (bad hello -> evt_error +
    close(4401) already sent; client disconnect -> nothing sent, the peer is
    gone). device_hint/presence feed the risk-based trust fusion (spec §6,
    §11) and default via vp.parse_hello when the client omits them."""
    try:
        hello = await ws.receive_text()
    except WebSocketDisconnect:
        return None
    try:
        parsed = vp.parse_hello(hello)
        email = verify_token_email(parsed["token"])
        return email, parsed["device_hint"], parsed["presence"]
    except (ValueError, PermissionError):
        await ws.send_text(json.dumps(vp.evt_error("Giriş doğrulanamadı")))
        await ws.close(code=4401)
        return None


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    await ws.accept()
    hs = await _handshake(ws)
    if hs is None:
        return
    email, device_hint, presence = hs
    from . import main

    try:
        # Runner acquisition is inside the try too: a cold-start infra hiccup
        # (Firestore client init, live-model resolution) must close the socket
        # gracefully with an error frame, not propagate unhandled through
        # Starlette (which has no websocket exception handler) -- matching how
        # the text path wraps its _init() inside run_turn's caller.
        runner, sessions, memory = main.get_voice_runner_sessions_memory()
        speaker_service = main.get_speaker_service()
        await VoiceBridge(
            runner, sessions, memory=memory, speaker_service=speaker_service,
            device_hint=device_hint, presence=presence,
        ).run(ws, user_id=email)
    except Exception:
        logging.exception("voice bridge failed for %s", email)
        await ws.send_text(json.dumps(vp.evt_error("Sesli oturum düştü, tekrar bağlan")))
        await ws.close(code=1011)
