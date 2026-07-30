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

from . import config, conversations, messages, repo_watch, speaker, voice, voice_manage, voice_trust
from .agent import AGENT_NAME
from .auth import require_scheduler, require_user

if TYPE_CHECKING:
    from .memory import Memory

APP_NAME = "jarvis"
app = FastAPI(title="JARVIS Brain")
app.include_router(voice.router)
app.include_router(voice_manage.router)

_runner: Runner | None = None
_session_service = InMemorySessionService()
_memory = None
_messages: "messages.MessageStore | None" = None
_conversations: "conversations.ConversationStore | None" = None
_voice_runner: Runner | None = None
_speaker_service: "speaker.SpeakerService | None" = None


def _build_text_model():
    """Decide the model for the text runners -- BOTH of them.

    Two shapes, both valid for ADK's Agent (str or BaseLlm):
    - config.LLM_BASE_URL empty: the resolved model NAME string (which is
      config.MODEL_NAME unless JARVIS_TEXT_MODEL pins one) -- the genai SDK
      then talks to AI Studio directly, exactly as before the proxy existed.
    - config.LLM_BASE_URL set: a google.adk.models.google_llm.Gemini INSTANCE
      bound to the proxy's base_url with the catalog-resolved model id. The
      base_url must NOT carry a "/v1beta" suffix (the genai SDK appends it;
      suffixing 404s every request) and auth needs no code (GOOGLE_API_KEY
      is sent as x-goog-api-key regardless) -- see app/text_model.py's
      module docstring for the live-verified details.

    The VOICE runner (_init_voice below) uses this same factory since the v2
    voice protocol: with STT/TTS on the device the server no longer opens a
    Gemini Live session at all -- voice turns are plain text turns
    (run_async), so there is no separate live model to resolve."""
    if not config.LLM_BASE_URL:
        return config.resolve_text_model()
    from google.adk.models.google_llm import Gemini

    return Gemini(model=config.resolve_text_model(), base_url=config.LLM_BASE_URL)


def _init() -> None:
    """Lazy init so tests can import the module without GCP credentials."""
    global _runner, _memory, _messages, _conversations
    if _runner is not None:
        return
    from google.cloud import firestore

    from .agent import build_agent
    from .memory import FirestoreAudit, Memory, make_e5_embedders

    db = firestore.Client()
    # e5 is asymmetric (passage vs query prefixes), so Memory gets the two
    # roles as separate callables -- see E5Embedders / Memory.__init__.
    # Cheap here: the model itself loads lazily on the first embed.
    embedders = make_e5_embedders()
    _memory = Memory(
        db, embed_fn=embedders.embed_passage, embed_query_fn=embedders.embed_query
    )
    _messages = messages.MessageStore(db)
    _conversations = conversations.ConversationStore(db)
    _runner = Runner(
        app_name=APP_NAME,
        agent=build_agent(_memory, FirestoreAudit(db), model=_build_text_model()),
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
            labels=config.SPEAKER_SAMPLE_LABELS,
            manual_cap=config.SPEAKER_MANUAL_CAP,
        )
    return _speaker_service


def _init_voice() -> None:
    """Lazy init for the voice-mode Runner: a SEPARATE Runner/Agent built on
    the SAME model factory as text chat (_build_text_model), sharing the SAME
    _session_service and _memory instances as the text-chat runner -- same
    memory, same audit trail, same tools/policy.

    Why a separate runner at all, if the model no longer differs? Because of
    trust_provider: only the voice agent gets one. Identity signals exist
    only for voice connections (the WS bridge publishes them per connection),
    and keeping the text runner provider-less makes it structurally unable to
    see them (app/voice_trust.py). There is no live model anymore -- the v2
    voice protocol moved STT/TTS onto the device, so voice turns arrive as
    text and run through run_async like any chat turn. The
    `if _voice_runner is not None: return` guard below makes this a
    once-per-process init."""
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
            _memory, FirestoreAudit(db), model=_build_text_model(),
            # ONLY the voice agent gets a trust provider: identity signals exist
            # only for live voice connections, and this keeps the text runner
            # structurally unable to see them (app/voice_trust.py).
            trust_provider=voice_trust.lookup,
        ),
        session_service=_session_service,
    )


def get_voice_runner_sessions_memory() -> "tuple[Runner, InMemorySessionService, Memory | None]":
    """Accessor for voice.py: the dedicated voice Runner (trust-provider
    wiring, see _init_voice) + the same Katman 1 session_service/memory
    instances as text chat, no privates touched."""
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
    # _conversations is None only in tests that monkeypatch _init to a no-op
    # and wire _messages/_runner directly (see tests/test_api.py's run_turn
    # tests); in the real process _init() (called above) always sets it
    # before this point, so the guard never fires there.
    if _conversations is not None:
        # touch() is ancillary bookkeeping for the conversation-list index,
        # not the primary feature -- the user's message is already durably
        # persisted via _messages.append() above. A Firestore hiccup here
        # must be logged and swallowed, never allowed to propagate up to
        # chat()'s broad except -> 502 and mask a reply that will still be
        # generated. A missed touch self-heals on this session's next
        # message via touch()'s upsert (see conversations.py).
        try:
            _conversations.touch(user_id, session_id, "user", message)
        except Exception:
            logging.exception(
                "run_turn: conversations.touch failed (user turn) for "
                "user_id=%s session_id=%s -- continuing without it",
                user_id, session_id,
            )
    content = types.Content(role="user", parts=[types.Part(text=message)])
    reply = ""
    async for event in _runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = event.content.parts[0].text or ""
    if reply:
        _messages.append(user_id, session_id, "model", reply)
        if _conversations is not None:
            # Same reasoning as the user-turn touch() above: the model's
            # reply is already durably persisted at this point, so a
            # bookkeeping failure here must not turn a good, already-stored
            # reply into a 502 with nothing to show for it.
            try:
                _conversations.touch(user_id, session_id, "model", reply)
            except Exception:
                logging.exception(
                    "run_turn: conversations.touch failed (model turn) for "
                    "user_id=%s session_id=%s -- continuing without it",
                    user_id, session_id,
                )
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


@app.get("/api/conversations")
async def list_conversations(email: str = Depends(require_user)):
    try:
        _init()
        return {"conversations": _conversations.list_conversations(user_id=email)}
    except Exception:
        logging.exception("conversations: list failed for user_id=%s", email)
        raise HTTPException(
            status_code=502,
            detail="Jarvis şu anda konuşmaları listeleyemiyor (altyapı hatası). Az sonra tekrar dene.",
        )


@app.delete("/api/conversations/{session_id}")
async def delete_conversation(session_id: str, email: str = Depends(require_user)):
    try:
        sid = messages.sanitize_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz oturum kimliği")
    try:
        _init()
        # Two independent writes, no shared transaction (see conversations.py's
        # module docstring for the full reasoning). Order matters: messages are
        # deleted FIRST, then the summary. A crash in between then fails closed
        # (a stale list row pointing at zero messages -- recoverable, harmless)
        # instead of failing open (summary gone, but the full transcript still
        # readable via GET /api/history -- a silent leak of a "deleted" chat).
        _messages.delete_session(user_id=email, session_id=sid)
        _conversations.delete(user_id=email, session_id=sid)
    except Exception:
        logging.exception(
            "conversations: delete failed for user_id=%s session_id=%s", email, sid
        )
        raise HTTPException(
            status_code=502,
            detail="Jarvis şu anda konuşmayı silemiyor (altyapı hatası). Az sonra tekrar dene.",
        )
    return {"deleted": sid}


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


@app.post("/api/jobs/repo-watch")
async def repo_watch_job(email: str = Depends(require_scheduler)):
    """Cloud Scheduler'ın saatlik tetiklediği repo kontrol turu; poller özetini döner."""
    try:
        _init()
        # asyncio.to_thread, enroll'daki gerekçenin aynısı: poll_once senkron
        # Firestore + GitHub ağ I/O'su yapar; loop'ta koşarsa /api/chat ve
        # /ws/voice'i de dondurur. make_client() de thread içinde (env okuma).
        return await asyncio.to_thread(
            lambda: repo_watch.poll_once(_memory.db, repo_watch.make_client())
        )
    except Exception:
        logging.exception("repo-watch job: poll_once failed")
        raise HTTPException(
            status_code=502,
            detail="Repo kontrolü şu an yapılamıyor (altyapı hatası). Az sonra tekrar dene.",
        )


_web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
