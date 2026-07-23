import logging
import os
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from . import config, messages, voice
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
        agent=build_agent(_memory, FirestoreAudit(db), model=config.resolve_live_model()),
        session_service=_session_service,
    )


def get_voice_runner_sessions_memory() -> "tuple[Runner, InMemorySessionService, Memory | None]":
    """Accessor for voice.py: dedicated live-model Runner + the same Katman 1
    session_service/memory instances as text chat, no privates touched."""
    _init_voice()
    return _voice_runner, _session_service, _memory


async def run_turn(user_id: str, session_id: str, message: str) -> str:
    _init()
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        session = await _session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    content = types.Content(role="user", parts=[types.Part(text=message)])
    reply = ""
    async for event in _runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = event.content.parts[0].text or ""
    _memory.snapshot_session(session_id, user_id, {"last_message": message, "last_reply": reply})
    return reply


class ChatRequest(BaseModel):
    session_id: str
    message: str


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
    _init()
    try:
        sid = messages.sanitize_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz oturum kimliği")
    return {"messages": _messages.history(user_id=email, session_id=sid)}


_web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
