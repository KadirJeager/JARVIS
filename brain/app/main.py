import asyncio
import base64
import logging
import os
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from . import config, messages, speaker, voice, voice_trust
from .agent import AGENT_NAME
from .auth import require_user

if TYPE_CHECKING:
    from .memory import Memory

APP_NAME = "jarvis"
app = FastAPI(title="JARVIS Brain")
app.include_router(voice.router)

_runner: Runner | None = None
_session_service = InMemorySessionService()
_memory = None
_messages: "messages.MessageStore | None" = None
_voice_runner: Runner | None = None
_speaker_service: "speaker.SpeakerService | None" = None


def _init() -> None:
    """Lazy init so tests can import the module without GCP credentials."""
    global _runner, _memory, _messages
    if _runner is not None:
        return
    from google.cloud import firestore

    from .agent import build_agent
    from .memory import FirestoreAudit, Memory, make_embed_fn

    db = firestore.Client()
    _memory = Memory(db, embed_fn=make_embed_fn())
    _messages = messages.MessageStore(db)
    _runner = Runner(
        app_name=APP_NAME,
        agent=build_agent(_memory, FirestoreAudit(db)),
        session_service=_session_service,
    )


def _enroll_db():
    """Firestore client accessor for speaker enrollment/identification: reuses
    the SAME client Memory already holds (_init() is idempotent), so this
    never opens a second Firestore connection."""
    _init()
    return _memory.db


def get_speaker_service() -> "speaker.SpeakerService":
    """Lazy singleton, mirroring _init_voice()'s pattern: built once per
    process from config's speaker thresholds, reused by every /ws/voice
    connection for live speaker identification (enroll below is a separate,
    simpler embed+store path that doesn't need a SpeakerService)."""
    global _speaker_service
    if _speaker_service is None:
        _speaker_service = speaker.SpeakerService(
            _enroll_db(),
            accept=config.SPEAKER_ACCEPT_THRESHOLD,
            adapt=config.SPEAKER_ADAPT_THRESHOLD,
            top_k=config.SPEAKER_TOPK,
            cap=config.SPEAKER_ADAPTIVE_CAP,
            history_cap=config.SPEAKER_HISTORY_CAP,
        )
    return _speaker_service


def _init_voice() -> None:
    """Lazy init for the voice-mode Runner: a SEPARATE Runner/Agent bound to
    config.resolve_live_model() (config.MODEL_NAME is not live-capable),
    sharing the SAME _session_service and _memory instances as the text-chat
    runner -- same memory, same audit trail, same tools/policy, only the
    model differs. See app/live_model.py + task-2a4-report.md /
    task-2a6-report.md for why the live model can't just be
    config.MODEL_NAME and how it's auto-resolved to the newest usable one.
    Resolution happens once, at first init, for the lifetime of this
    process -- the `if _voice_runner is not None: return` guard below
    covers that."""
    global _voice_runner
    if _voice_runner is not None:
        return
    _init()  # ensures _session_service/_memory exist and are shared
    from google.cloud import firestore

    from .agent import build_agent
    from .memory import FirestoreAudit

    db = firestore.Client()
    _voice_runner = Runner(
        app_name=APP_NAME,
        agent=build_agent(
            _memory, FirestoreAudit(db), model=config.resolve_live_model(),
            # ONLY the voice agent gets a trust provider: identity signals exist
            # only for live voice connections, and this keeps the text runner
            # structurally unable to see them (app/voice_trust.py).
            trust_provider=voice_trust.lookup,
        ),
        session_service=_session_service,
    )


def get_voice_runner_sessions_memory() -> "tuple[Runner, InMemorySessionService, Memory | None]":
    """Accessor for voice.py: dedicated live-model Runner + the same Katman 1
    session_service/memory instances as text chat, no privates touched."""
    _init_voice()
    return _voice_runner, _session_service, _memory


async def _ensure_session(user_id: str, session_id: str):
    """Get the ADK session, or create it and rehydrate from the persistent
    transcript so a cold-started instance keeps conversational context."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is not None:
        return session
    session = await _session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    for msg in _messages.history(user_id, session_id):
        if not msg["text"]:
            # Defensively skip any stored message with empty text (e.g. a
            # pre-existing empty-reply row) -- Gemini rejects empty text
            # parts (INVALID_ARGUMENT), which would brick every turn in
            # this session on replay. run_turn no longer persists these,
            # but old rows may already exist.
            continue
        # Event.author must match the agent's own name for model turns (and
        # "user" for user turns) -- NOT the raw Gemini content role ("model").
        # ADK's context builder (_is_other_agent_reply) treats any event whose
        # author isn't the current agent's name (or "user") as a different
        # agent's reply and rewrites it into a synthetic "For context: [model]
        # said: ..." user turn, which would defeat rehydration on cold start.
        # Live events don't hit this because ADK sets author=agent.name itself.
        author = AGENT_NAME if msg["role"] == "model" else "user"
        event = Event(
            author=author,
            content=types.Content(
                role=msg["role"], parts=[types.Part(text=msg["text"])]
            ),
        )
        await _session_service.append_event(session, event)
    logging.info(
        "rehydrate: user=%s session=%s events=%d",
        user_id, session_id, len(session.events),
    )
    return session


async def run_turn(user_id: str, session_id: str, message: str) -> str:
    _init()
    await _ensure_session(user_id, session_id)
    _messages.append(user_id, session_id, "user", message)
    content = types.Content(role="user", parts=[types.Part(text=message)])
    reply = ""
    async for event in _runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = event.content.parts[0].text or ""
    if reply:
        _messages.append(user_id, session_id, "model", reply)
    return reply


class ChatRequest(BaseModel):
    session_id: str
    message: str


class EnrollRequest(BaseModel):
    clips: list[str]  # base64-encoded PCM16 mono 16kHz utterances
    device_hint: str = "unknown"


# /healthz is intercepted by Google Frontend on run.app (returns Google's own
# 404 before reaching the container) — the canonical health path is /api/health;
# /healthz is kept for local convenience only.
@app.get("/api/health")
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest, email: str = Depends(require_user)):
    try:
        sid = messages.sanitize_session_id(req.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz oturum kimliği")
    try:
        reply = await run_turn(user_id=email, session_id=sid, message=req.message)
    except Exception:
        logging.exception("chat: run_turn failed for user_id=%s session_id=%s", email, sid)
        raise HTTPException(
            status_code=502,
            detail="Jarvis şu anda cevap veremiyor (altyapı hatası). Az sonra tekrar dene.",
        )
    return {"reply": reply}


@app.get("/api/history")
async def history(session_id: str, email: str = Depends(require_user)):
    try:
        sid = messages.sanitize_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz oturum kimliği")
    try:
        _init()
        return {"messages": _messages.history(user_id=email, session_id=sid)}
    except Exception:
        logging.exception("history: init or read failed for user_id=%s session_id=%s", email, sid)
        raise HTTPException(
            status_code=502,
            detail="Jarvis şu anda geçmişi getiremiyor (altyapı hatası). Az sonra tekrar dene.",
        )


@app.post("/api/voice/enroll")
async def enroll(req: EnrollRequest, email: str = Depends(require_user)):
    if not req.clips:
        raise HTTPException(status_code=400, detail="En az bir ses klibi gerekli")
    try:
        # OFF THE EVENT LOOP, for the same reason the live verify path is
        # (app/voice.py): speaker.embed is real ECAPA inference plus, on the
        # first call in a process, the ~89 MB lazy model load -- seconds of
        # blocking CPU, multiplied by the number of clips. Run inline in this
        # async endpoint it stalls the whole loop, and jarvis-brain serves
        # /api/chat and /ws/voice from that same loop.
        vecs = await asyncio.to_thread(
            lambda: [speaker.embed(base64.b64decode(clip)) for clip in req.clips]
        )
        # Via SpeakerService, NOT speaker_store directly: enrollment's
        # load->extend->save must share the gallery lock with identify()'s
        # adapt path, which now really can run concurrently in a worker thread
        # (see SpeakerService.enroll).
        #
        # ALSO off the loop, and for a reason the embedding above does not
        # cover: sharing that lock means this call can BLOCK on it, and the
        # holder is identify() in a worker thread, keeping it across two
        # Firestore round-trips. Awaited inline, an enrollment landing during a
        # live utterance would freeze the loop -- every WS connection and every
        # /api/chat turn on this instance -- until those RPCs returned.
        # get_speaker_service() is INSIDE the thread too: on the first call it
        # builds the Firestore client (credential discovery, possibly a metadata
        # server round-trip), which is not something to do on the loop either.
        total = await asyncio.to_thread(
            lambda: get_speaker_service().enroll(email, vecs, device_hint=req.device_hint)
        )
    except Exception:
        logging.exception("enroll: failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail="Ses kaydı işlenemedi, tekrar dene")
    return {"anchors": total}


_web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
