import os

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from .auth import require_user

APP_NAME = "jarvis"
app = FastAPI(title="JARVIS Brain")

_runner: Runner | None = None
_session_service = InMemorySessionService()
_memory = None


def _init() -> None:
    """Lazy init so tests can import the module without GCP credentials."""
    global _runner, _memory
    if _runner is not None:
        return
    from google.cloud import firestore

    from .agent import build_agent
    from .memory import FirestoreAudit, Memory, make_embed_fn

    db = firestore.Client()
    _memory = Memory(db, embed_fn=make_embed_fn())
    _runner = Runner(
        app_name=APP_NAME,
        agent=build_agent(_memory, FirestoreAudit(db)),
        session_service=_session_service,
    )


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
    reply = await run_turn(user_id=email, session_id=req.session_id, message=req.message)
    return {"reply": reply}


_web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
