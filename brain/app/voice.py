"""Voice gateway: bridges a WebSocket to ADK TEXT turns (protocol v2).

Contract: see voice_protocol.py. v1 bridged mic PCM to a Gemini Live session
(run_live) and streamed model audio back; v2 moved STT/TTS onto the device.
The client's on-device SpeechRecognizer sends FINAL text as `user_text`
frames; the mic PCM keeps flowing ONLY so the speaker-ID machine has audio to
score; the server runs each utterance as a plain text turn
(runner.run_async, same final-response collection pattern as main.run_turn)
and answers with an evt_jarvis_text event the device's TTS speaks. No binary
frame ever leaves the server.

The bridge is handed a runner built by main._init_voice: the SAME model
factory as text chat (_build_text_model -- there is no live model to resolve
anymore), but wired with trust_provider=voice_trust.lookup so the identity
signals below reach the policy layer, and sharing the text path's
session_service and memory instances.

Speaker identity (Katman 2b Dilim 3a): mic PCM is buffered in a BOUNDED
rolling window and verified ONCE per utterance, at the utterance boundary --
which in v2 is the client's `user_text` frame with utterance_final=true (the
device's own STT endpointer decides where utterances end; the server no
longer infers boundaries from live transcriptions or turn_complete). The
verification runs off the event loop via asyncio.to_thread, and the resulting
trust level is published through app/voice_trust.py BEFORE the text turn is
started: the policy callback reads it during that turn's tool calls. It is
NOT written into session.state: ADK 1.36.2 hands the bridge and the runner
independent session copies, so such a write never reaches the policy
callback -- see voice_trust.py for the source-verified detail.
"""
import asyncio
import contextlib
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from google.genai import types

from . import antispoof, config, trust, vitals, voice_challenge, voice_trust
from . import voice_protocol as vp
from .auth import require_google_user, verify_bearer_email

router = APIRouter()

APP_NAME = "jarvis"   # must match main.APP_NAME: it is part of the ADK session identity

# Echo-window estimate (see VoiceBridge._speech_seconds). Only ever suppresses
# ADAPTATION, so every constant here is chosen to err long.
SPEECH_CHARS_PER_SECOND = 14.0
SPEECH_START_MARGIN_SECONDS = 1.0
SPEECH_MAX_SECONDS = 60.0

active_bridges: dict[str, "VoiceBridge"] = {}


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
        self._utterance = bytearray()   # accumulates this utterance's mic PCM
        self._user_id = ""
        self._session_id = ""
        self._trust_key: voice_trust.SessionKey | None = None
        self._ws: WebSocket | None = None
        self._pending_challenge_code: str | None = None
        # The speech_start ONSET latch.
        self._speech_open = False
        # SERIALIZES text turns: run one run_async turn at a time, queue the rest FIFO.
        self._turn_lock = asyncio.Lock()
        # Strong refs to in-flight turn tasks so teardown can cancel them.
        self._turn_tasks: set[asyncio.Task] = set()
        # Set when the peer is gone.
        self._closed = False
        # Identifies THIS connection in the shared trust registry.
        self._owner = uuid.uuid4().hex
        # Monotonic deadline for TTS playback estimate.
        self._speaking_until = 0.0

    def _get_db(self):
        if self.memory is not None and getattr(self.memory, "db", None) is not None:
            return self.memory.db
        try:
            from . import main
            return main._enroll_db()
        except Exception:
            return None

    async def prompt_challenge(self, code: str) -> bool:
        """Prompt user via TTS over WebSocket with 4-digit challenge code."""
        self._pending_challenge_code = code
        words = voice_challenge.digit_to_words(code)
        msg = f"Doğrulama kodunuz: {words}"
        if self._ws is None or self._closed:
            return False
        sent = await self._safe_send(self._ws, vp.evt_jarvis_text(msg))
        if sent:
            await self._safe_send(self._ws, vp.evt_transcript("jarvis", msg))
        return sent

    # Seam so tests can move time without sleeping. Monotonic, not wall clock:
    # this is a duration guard and must not jump with an NTP correction.
    _now = staticmethod(time.monotonic)

    @staticmethod
    def _speech_seconds(reply: str) -> float:
        """Conservative estimate of how long the device will take to SAY `reply`.

        Turkish TTS at a normal rate runs roughly 14 characters per second;
        the constant is deliberately on the slow side and a flat margin is
        added for engine start-up, because every error in the generous
        direction costs only a skipped adaptive sample. Clamped so a
        pathological reply cannot disable adaptation for the rest of the call.
        """
        return min(
            SPEECH_MAX_SECONDS,
            len(reply) / SPEECH_CHARS_PER_SECOND + SPEECH_START_MARGIN_SECONDS,
        )

    def _publish_trust(self, level: str, voice_score: float | None, cm_ok: bool | None = None) -> None:
        """Make this connection's identity signals visible to the policy layer.
        No-op before run() has established the session key. See voice_trust.py
        for why this cannot just be a session.state write."""
        if self._trust_key is None:
            return
        voice_trust.publish(self._trust_key, voice_trust.VoiceSignals(
            trust_level=level, voice_score=voice_score,
            presence=self.presence, device_hint=self.device_hint,
            cm_ok=cm_ok, owner=self._owner,
        ))

    async def _safe_send(self, ws, payload: dict) -> bool:
        """Send one JSON event, or mark the bridge closed if the peer is gone.

        Returns False once the socket is dead so callers can stop producing
        output for a client that will never see it. Swallows the RuntimeError
        that starlette/uvicorn raise on send-after-close (a client that hung
        up mid-turn is ordinary, not an exception) and WebSocketDisconnect;
        anything else still propagates."""
        if self._closed:
            return False
        try:
            await ws.send_text(json.dumps(payload))
            return True
        except (RuntimeError, WebSocketDisconnect):
            self._closed = True
            return False

    async def _receive_once(self, ws) -> bool:
        """Read one client message: buffer mic PCM for speaker-ID, or handle a
        JSON control frame (speech_start / user_text).

        Returns False when the client disconnected (caller should stop looping).
        """
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            return False
        if data := msg.get("bytes"):
            if self.speaker_service is not None:
                self._utterance.extend(data)
                # BOUNDED buffer: the drain only happens at an utterance
                # boundary (a user_text final), and that frame is not
                # guaranteed to arrive (the device VAD can sit through long
                # silence without ever finalizing). Unbounded, this grows at
                # AUDIO_IN_RATE*2 = 32 KB/s (~115 MB/h) inside a process that
                # already carries torch. Keeping only the most recent window
                # also bounds inference time, and a window that long is far
                # more audio than ECAPA needs.
                if len(self._utterance) > config.SPEAKER_UTTERANCE_MAX_BYTES:
                    del self._utterance[:-config.SPEAKER_UTTERANCE_MAX_BYTES]
        elif text := msg.get("text"):
            await self._handle_text_frame(ws, text)
        return True

    async def _handle_text_frame(self, ws, raw: str) -> None:
        """Dispatch one client JSON frame. Unknown types and malformed JSON
        are logged and ignored -- a stray frame must never break the audio
        stream (same failure philosophy as _verify_utterance)."""
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning("voice bridge: non-JSON text frame from %s ignored", self._user_id)
            return
        frame_type = frame.get("type") if isinstance(frame, dict) else None
        if frame_type == "speech_start":
            # The device's STT heard speech begin (barge-in included). Trim the
            # buffer to the ONSET window: the mic never stopped, so the buffer
            # is [audio collected since the last boundary][this utterance so
            # far]. The device reports speech onset within a few hundred ms, so
            # the tail is the utterance and everything before it is inter-turn
            # room noise -- which speaker.embed would average straight into the
            # embedding.
            #
            # ONLY on the FIRST speech_start of an utterance. "Where did the
            # speech start?" is an open question once; on a repeat the audio in
            # front of the window is the user's own speech, already
            # accumulating, and trimming again would score whatever 0.5 s
            # happened to be at the tail. The latch resets at the utterance
            # boundary (_on_utterance_final).
            if not self._speech_open:
                self._speech_open = True
                del self._utterance[:-config.SPEAKER_BARGE_IN_ONSET_BYTES]
        elif frame_type == "user_text":
            text = frame.get("text") or ""
            if not frame.get("utterance_final") or not text.strip():
                # NOT an error: a non-final user_text is a partial STT result
                # (informational; the v2 contract only acts on finals), and an
                # empty final is a false VAD trigger with nothing to answer.
                # evt_error is user-visible alarm UI; routine partials must not
                # trip it. Log with the DATA that would localize a misfire.
                logging.info(
                    "voice bridge: user_text ignored for %s (final=%s, %d chars) -- "
                    "not an utterance boundary",
                    self._user_id, frame.get("utterance_final"), len(text),
                )
                return
            await self._on_utterance_final(ws, text)
        else:
            logging.info(
                "voice bridge: unknown client frame type %r from %s ignored",
                frame_type, self._user_id,
            )

    async def _on_utterance_final(self, ws, text: str) -> None:
        """The utterance boundary: verify the buffered PCM, publish trust, then
        queue the text turn.

        ORDER IS LOAD-BEARING: the trust publish must land BEFORE the turn's
        run_async starts, because the policy callback reads it during THIS
        turn's tool calls. Verification therefore runs inline here (in the
        receive loop), not inside the turn task: a turn queued behind the lock
        would otherwise run its tool calls under the PREVIOUS utterance's
        level. The cost -- the receive loop pauses on off-thread inference --
        is acceptable: PCM frames arriving meanwhile are socket-buffered and
        belong to the NEXT utterance anyway, and the same pause existed in v1
        (verification was awaited inline in the event pump)."""
        self._speech_open = False    # this utterance ended; next speech_start trims anew
        if self.speaker_service is not None:
            # min_bytes=0: the STT final is itself the positive signal that a
            # whole utterance preceded it, so short commands ("evet") must
            # still be verified. An empty buffer is a no-op inside.
            await self._verify_utterance(ws, min_bytes=0)

        # Check for active voice challenge response
        db = self._get_db()
        pending_code = self._pending_challenge_code
        if not pending_code and db is not None:
            pending_doc = voice_challenge.get_pending_challenge(db, self._user_id)
            if pending_doc:
                pending_code = pending_doc.get("code")

        if pending_code:
            extracted_digits = voice_challenge.extract_digits_from_text(text)
            matched = (pending_code in extracted_digits)
            granted = False
            if matched and db is not None:
                granted = voice_challenge.verify_and_grant(db, self._user_id, pending_code)

            self._pending_challenge_code = None
            if granted:
                reply = "Doğrulama kodu kabul edildi."
                await self._safe_send(ws, vp.evt_jarvis_text(reply))
                await self._safe_send(ws, vp.evt_transcript("jarvis", reply))
                await self._safe_send(ws, vp.evt_turn_complete())
                return
            else:
                reply = "Doğrulama kodu hatalı veya süresi dolmuş."
                await self._safe_send(ws, vp.evt_jarvis_text(reply))
                await self._safe_send(ws, vp.evt_transcript("jarvis", reply))
                await self._safe_send(ws, vp.evt_turn_complete())
                return

        task = asyncio.create_task(self._run_turn(ws, text))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    async def _run_turn(self, ws, text: str) -> None:
        """One text turn, serialized by _turn_lock. Never raises: the task is
        fire-and-forget from the receive loop, so any escape would surface as
        asyncio's 'exception was never retrieved' and lose the failure."""
        try:
            async with self._turn_lock:
                await self._serve_turn(ws, text)
        except asyncio.CancelledError:
            raise   # teardown cancelling an in-flight turn: propagate, don't log as failure
        except Exception:
            logging.exception("voice bridge: turn task failed for %s", self._user_id)

    async def _serve_turn(self, ws, text: str) -> None:
        """Run the ADK text turn and answer: jarvis_text (for the device TTS),
        the jarvis transcript row (UI history + snapshot), and turn_complete.
        A runner failure is answered with evt_error + turn_complete so the
        client's 'thinking' state always terminates; the connection stays up
        for the next utterance."""
        self.transcript.append({"role": "user", "text": text})
        reply = ""
        content = types.Content(role="user", parts=[types.Part(text=text)])
        try:
            # Same final-response collection pattern as main.run_turn: later
            # finals overwrite earlier ones, and a missing/empty text part
            # stays "".
            async for event in self.runner.run_async(
                user_id=self._user_id, session_id=self._session_id, new_message=content
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    reply = event.content.parts[0].text or ""
        except Exception:
            logging.exception("voice bridge: text turn failed for %s", self._user_id)
            await self._safe_send(ws, vp.evt_error("İstek işlenemedi, tekrar dene"))
            # NOT: _speaking_until burada sıfırlanmaz. Bu dalda hiç jarvis_text
            # gitmedi, yani zaten bir konuşma penceresi açılmadı; açılmış olsaydı
            # da onu erken kapatmak yalnızca adaptasyonu erken serbest bırakırdı.
            await self._safe_send(ws, vp.evt_turn_complete())
            return
        if reply:
            # jarvis_text FIRST (it is what the TTS speaks), then the
            # transcript row for the UI history; both before turn_complete.
            self._speaking_until = self._now() + self._speech_seconds(reply)
            await self._safe_send(ws, vp.evt_jarvis_text(reply))
            await self._safe_send(ws, vp.evt_transcript("jarvis", reply))
            self.transcript.append({"role": "jarvis", "text": reply})
        else:
            # An empty final response is a real model outcome (e.g. a turn
            # that only called tools). Sending an empty jarvis_text would make
            # the device TTS say nothing after a thinking pause -- log it as
            # DATA instead and still close the turn.
            logging.info("voice bridge: empty model reply for %s -- no jarvis_text", self._user_id)
        await self._safe_send(ws, vp.evt_turn_complete())
        # North Star §4.5: başarıyla kapanan her ses turu voice_turns_today'i
        # artırır. Sayaç hatası turu asla bozmaz (main.run_turn'deki
        # conversations.touch deseni); memory'siz test bridge'leri atlanır.
        if self.memory is not None:
            try:
                vitals.bump(self.memory.db, "voice_turns_today")
            except Exception:
                logging.exception(
                    "voice bridge: vitals sayacı yazılamadı for %s -- tura devam", self._user_id
                )

    async def _verify_utterance(self, ws, min_bytes: int = 0) -> None:
        """Called at the user utterance boundary: run anti-spoofing (CM) check,
        run speaker identity on the buffered PCM, fuse into a trust level,
        publish it for the policy layer, and tell the client. Any failure is
        logged and treated as unverified/fail-safe -- it must never break the audio stream.

        `min_bytes` is the caller's floor. The v2 boundary (a user_text final)
        always passes 0: the device's STT already told us the buffer holds a
        whole utterance, and short commands must still be verified. Below the
        floor we publish NOTHING -- the per-connection baseline stands, which
        under locked/ambient is MEDIUM, never HIGH."""
        pcm = bytes(self._utterance)
        if len(pcm) < min_bytes:
            # Declined, so NOT drained: this function must not throw away audio
            # it refused to score. The drain is the boundary's decision to
            # make, not this one's.
            logging.info(
                "voice trust: skipped verification for %s, %d bytes buffered is "
                "below the %d-byte (%.2f s) floor -- fragment, not an utterance",
                self._user_id, len(pcm), min_bytes,
                min_bytes / (vp.AUDIO_IN_RATE * 2),
            )
            return
        self._utterance.clear()
        if not pcm:
            return

        cm_ok: bool | None = None
        cm_fake_prob: float | None = None

        if config.CM_ENABLED:
            try:
                cm_is_bonafide, cm_fake_prob = await asyncio.wait_for(
                    asyncio.to_thread(antispoof.is_bonafide, pcm),
                    config.CM_TIMEOUT_S,
                )
                cm_ok = cm_is_bonafide
                if not cm_ok:
                    logging.warning(
                        "voice CM: SPOOF REDDI user=%s fake_prob=%.4f threshold=%.2f bytes=%d",
                        self._user_id, cm_fake_prob, config.CM_REJECT_THRESHOLD, len(pcm),
                    )
            except Exception:
                logging.exception("voice bridge: CM evaluation failed for %s", self._user_id)
                cm_ok = None
                cm_fake_prob = None

        try:
            # OFF THE EVENT LOOP: real ECAPA inference (plus the ~89 MB lazy
            # model load on the very first call) is seconds of blocking CPU. On
            # the loop it would stall this session's audio, its receive loop,
            # and every other WS connection this instance serves.
            # speaker._get_model is lock-guarded precisely because this makes
            # concurrent first calls possible.
            outcome = await asyncio.to_thread(
                self.speaker_service.identify,
                self._user_id, pcm, self.device_hint, auth_is_kadir=True,
                allow_adapt=self._now() >= self._speaking_until,
                cm_ok=cm_ok,
            )
            verified, score = outcome.verified, outcome.score
        except Exception:
            logging.exception("voice bridge: speaker.identify failed for %s", self._user_id)
            outcome, verified, score = None, False, 0.0
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
        self._publish_trust(level, score, cm_ok=cm_ok)
        await self._safe_send(ws, vp.evt_speaker("user", verified, score, cm_ok=cm_ok))
        # History AFTER the trust publish and the client event: those two are
        # the turn's safety-relevant outputs, the history row is observability
        # (spec §4.2) -- it must neither delay nor break them. No row on the
        # identify-failure path: there is no embedding a correction could feed
        # back, and the failure is already logged above.
        if outcome is not None:
            try:
                await asyncio.to_thread(
                    self.speaker_service.record_history, self._user_id,
                    score=outcome.score, verified=outcome.verified,
                    vec=outcome.vec, device_hint=self.device_hint,
                    presence=self.presence, trust_level=level,
                    adapted_sample_id=outcome.adapted_sample_id,
                    cm_fake_prob=cm_fake_prob,
                )
            except Exception:
                logging.exception(
                    "voice bridge: history record failed for %s", self._user_id)
        logging.info(
            "voice trust: user=%s verified=%s score=%.4f presence=%s device=%s level=%s cm_ok=%s cm_fake_prob=%s",
            self._user_id, verified, score, self.presence, self.device_hint, level, cm_ok, cm_fake_prob,
        )

    async def run(self, ws: WebSocket, user_id: str) -> None:
        self._ws = ws
        self._user_id = user_id
        active_bridges[user_id] = self
        session_id = f"voice-{user_id}"
        # The session must EXIST before the first turn: Runner.run_async's
        # auto_create_session defaults to False (runners.py), so the runner's
        # own _get_or_create_session would raise SessionNotFoundError
        # otherwise. The returned object is only ADK's copy -- never a channel
        # back to the runner (voice_trust.py) -- so nothing is kept from it.
        session = await self.session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if session is None:
            await self.session_service.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )
        self._session_id = session_id
        self._trust_key = voice_trust.key_for(APP_NAME, user_id, session_id)
        if self.speaker_service is not None:
            # Initialize THIS connection's trust from its own context: a new
            # connection must never inherit a previous one's level, and any
            # tool call that lands before the first utterance is verified must
            # already see the tightened, no-voice-evidence-yet level rather
            # than the HIGH default.
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
        try:
            # The receive loop is the ONLY pump: mic PCM and control frames
            # arrive on the same socket, and turns run as tasks (see
            # _on_utterance_final) so a multi-second run_async never blocks
            # the PCM feeding the NEXT utterance's speaker-ID buffer.
            while await self._receive_once(ws):
                pass
        finally:
            active_bridges.pop(self._user_id, None)
            # The peer is gone (or the loop broke): anything still queued has
            # nowhere to send, so flag it BEFORE awaiting the turn tasks --
            # a task mid-run_async finishes into _safe_send no-ops instead of
            # send-after-close RuntimeErrors.
            self._closed = True
            # Outermost teardown. First stop in-flight turns: the socket is
            # gone, so their answers have nowhere to go, and their transcript
            # rows would land after the snapshot below. Then drop this
            # connection's trust: its identity evidence is stale, and a
            # leftover entry would be visible to any later tool call on the
            # same ADK session id (which is per-USER and outlives the
            # connection).
            for task in list(self._turn_tasks):
                task.cancel()
            for task in list(self._turn_tasks):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if self._trust_key is not None:
                # Compare-and-delete: a sibling connection for the same user
                # shares this key, so only OUR own entry may be removed.
                voice_trust.clear(self._trust_key, self._owner)
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
    or None if the handshake did not complete. Failure paths: bad hello or bad
    token -> evt_error + close(4401); a hello WITHOUT client_caps -> a v1
    client asking for the retired Gemini Live bridge -> evt_error("Uygulamayı
    güncelle") + close(4409) (there is no server-side audio path left to serve
    it); client disconnect -> nothing sent, the peer is gone.
    device_hint/presence feed the risk-based trust fusion (spec §6, §11) and
    default via vp.parse_hello when the client omits them."""
    try:
        hello = await ws.receive_text()
    except WebSocketDisconnect:
        return None
    try:
        parsed = vp.parse_hello(hello)
        # verify_bearer_email Google'ın JWKS uçlarına (Google token) veya
        # Firestore'a (cihaz token) giden blocking I/O yapar -- guest_gate.py
        # _GuestAuth ile aynı taşıma: event loop'u bloklamamak için thread'de
        # çalışır (final review Important 2b).
        email = await asyncio.to_thread(verify_bearer_email, parsed["token"])
    except (ValueError, PermissionError):
        await ws.send_text(json.dumps(vp.evt_error("Giriş doğrulanamadı")))
        await ws.close(code=4401)
        return None
    if not parsed["client_caps"]:
        # v1 client: authenticated fine, but speaks a protocol the server no
        # longer serves. Distinct close code (4409) so the client can tell
        # "upgrade required" apart from "auth failed" (4401).
        await ws.send_text(json.dumps(vp.evt_error("Uygulamayı güncelle")))
        await ws.close(code=4409)
        return None
    return email, parsed["device_hint"], parsed["presence"]


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
        # (Firestore client init, first-init model resolution) must close the
        # socket gracefully with an error frame, not propagate unhandled through
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
        # The bridge may have died BECAUSE the socket is already closed (the
        # RuntimeError-on-send path seen in prod logs): sending the error
        # frame would then raise the very same send-after-close RuntimeError
        # and produce a second, misleading traceback. Suppress it -- a dead
        # peer cannot be told anything anyway.
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await ws.send_text(json.dumps(vp.evt_error("Sesli oturum düştü, tekrar bağlan")))
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await ws.close(code=1011)


@router.post("/api/voice/challenge")
async def create_challenge_endpoint(email: str = Depends(require_google_user)):
    """Request a voice challenge for liveness verification during enrollment.

    Generates a 4-digit challenge code in Firestore.
    If an active WebSocket voice bridge exists for email, speaks the code via TTS.
    """
    from . import main
    db = main._enroll_db()
    code, _doc = voice_challenge.create_challenge(db, email)

    bridge = active_bridges.get(email)
    code_spoken = False
    if bridge is not None and not bridge._closed:
        code_spoken = await bridge.prompt_challenge(code)

    return {
        "status": "challenge_created",
        "code_spoken": code_spoken,
    }
