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
from pydantic import BaseModel, Field, field_validator

from . import antispoof, approvals, auth, config, conversations, device_tokens, events, fcm, guest_gate, messages, reminders, repo_watch, speaker, tool_registry, vitals, voice, voice_challenge, voice_manage, voice_trust
from .agent import AGENT_NAME
from .auth import require_google_user, require_scheduler, require_user

if TYPE_CHECKING:
    from .memory import Memory

# INFO logları Cloud Logging'e düşsün: root logger yapılandırılmazsa WARNING'de
# kalır ve uygulamanın bütün iz satırları (approval_sink:/fcm:/factory:) üretimde
# görünmez — 4 Ağu 01:19 vakasında onay push'unun akıbeti bu yüzden teşhis
# edilemedi. uvicorn root'a handler koymaz -> üretimde basicConfig dalı çalışır;
# pytest kendi handler'larını koyar -> orada yalnız seviye düşürülür (basicConfig
# handler varken sessizce hiçbir şey yapmazdı).
if logging.getLogger().handlers:
    logging.getLogger().setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO)

APP_NAME = "jarvis"
app = FastAPI(title="JARVIS Brain")
app.include_router(voice.router)
app.include_router(voice_manage.router)


@app.on_event("startup")
async def _guest_gate_start() -> None:
    """Misafir Kapısı'nın (§4.9) MCP session manager'ını aç — request'lerden
    önce run() context'i aktif olmalı (app/guest_gate.py docstring)."""
    await guest_gate.start()


@app.on_event("shutdown")
async def _guest_gate_stop() -> None:
    await guest_gate.stop()


@app.on_event("startup")
async def _warm_heavy_models() -> None:
    """Pre-load the lazy heavy models at process start, off the event loop.

    Prod evidence (2026-07-31): the FIRST voice utterance after a cold start
    stalled ~20 s while ECAPA downloaded-and-loaded (its lazy singleton), and
    the first memory search in the same window stalled again for the ~1.1 GB
    e5 load. The user experiences that as "çok geç tepki veriyor" plus
    client-side connect timeouts. Both loaders are already lock-guarded
    singletons (speaker._get_model, memory._get_e5_model), so warming them
    here only moves the inevitable cost from the first live request to
    startup -- where Cloud Run's startup probe already waits. Failures are
    logged, never fatal: the lazy path still works as the fallback.
    """
    import threading

    from . import memory as memory_mod
    from . import speaker as speaker_mod

    def _warm() -> None:
        for name, load in _warmup_targets():
            try:
                load()
                logging.info("warmup: %s model loaded", name)
            except Exception:
                logging.exception("warmup: %s model failed to pre-load (lazy path remains)", name)

    threading.Thread(target=_warm, name="model-warmup", daemon=True).start()


def _warmup_targets() -> list:
    """Which heavy models this process pre-loads.

    e5 and ECAPA are warmed everywhere. The CM model is warmed ONLY where it is
    opted in, because the same image serves both jarvis-brain and jarvis-voice:
    the brain never calls `antispoof.is_bonafide`, so its lazy singleton stays
    unloaded there, and warming its ~1.2 GiB unconditionally would push the 3Gi
    brain container into exactly the OOM that took the 4Gi voice container down
    on 2026-08-10. Opt-in is explicit rather than sniffing `K_SERVICE`: the
    service that needs it should say so.
    """
    from . import antispoof as antispoof_mod
    from . import memory as memory_mod
    from . import speaker as speaker_mod

    targets = [("e5", memory_mod._get_e5_model), ("ecapa", speaker_mod._get_model)]
    if config.CM_ENABLED and config.CM_WARMUP:
        targets.append(("cm", antispoof_mod._get_model))
    return targets


_runner: Runner | None = None
_session_service = InMemorySessionService()
_memory = None
_audit = None  # FirestoreAudit; Misafir Kapısı (guest_gate) da bunu paylaşır
_messages: "messages.MessageStore | None" = None
_conversations: "conversations.ConversationStore | None" = None
_voice_runner: Runner | None = None
_speaker_service: "speaker.SpeakerService | None" = None


def _build_text_model(voice: bool = False):
    """Decide the model for the text runners.

    Two shapes, both valid for ADK's Agent (str or BaseLlm):
    - config.LLM_BASE_URL empty: the resolved model NAME string (which is
      config.MODEL_NAME unless the relevant pin env is set) -- the genai SDK
      then talks to AI Studio directly, exactly as before the proxy existed.
    - config.LLM_BASE_URL set: a google.adk.models.google_llm.Gemini INSTANCE
      bound to the proxy's base_url with the catalog-resolved model id. The
      base_url must NOT carry a "/v1beta" suffix (the genai SDK appends it;
      suffixing 404s every request) and auth needs no code (GOOGLE_API_KEY
      is sent as x-goog-api-key regardless) -- see app/text_model.py's
      module docstring for the live-verified details.

    voice=True (the _init_voice runner) resolves with latency_first: a voice
    conversation is real-time, so the fastest usable flash variant wins over
    the thinking-heavy one the text chat prefers (prod complaint 2026-07-31:
    "çok geç tepki veriyor"). The v2 voice protocol moved STT/TTS onto the
    device, so voice turns are plain text turns (run_async) -- there is no
    separate live model to resolve, only this variant preference."""
    resolve = config.resolve_voice_model if voice else config.resolve_text_model
    if not config.LLM_BASE_URL:
        return resolve()
    from google.adk.models.google_llm import Gemini

    return Gemini(model=resolve(), base_url=config.LLM_BASE_URL)


APPROVAL_PENDING_REPLY = (
    "ONAY KARTI GÖNDERİLDİ: '{tool_name}' kırmızı bölgede — Kadir'in kararını "
    "bekliyorum. Kadir'e onay kartı gönderdiğini söyle ve BEKLE; aynı istek için "
    "kartı tekrar oluşturma."
)


def _approval_card_texts(tool_name: str, args: dict) -> tuple[str, str, str]:
    """Onay kartının üç Türkçe metni: başlık, detay, sohbete düşecek kart.

    Argüman özeti kart için 120 karakterde kesilir; TAM argümanlar zaten onay
    dokümanında (approvals.request audit'le aynı 500 karakter kuralıyla yazar).
    Kart, Kadir'in neye onay verdiğini görmesi içindir, tam yük dökümü için
    değil."""
    ozet = ", ".join(f"{k}={str(v)[:120]}" for k, v in (args or {}).items()) or "argümansız"
    title = f"'{tool_name}' çalıştırılsın mı?"
    detail = (f"Kırmızı bölge eylemi: {tool_name}({ozet}). "
              "Onayın olmadan çalıştırmıyorum.")
    card = f"🔔 Onay bekliyor — {title}\n{detail}"
    return title, detail, card


def _approval_sink():
    """Kırmızı bölge engelini bekleyen bir onaya çeviren sink'i kurar (spec §5).

    `policy.make_policy_callback`'e verilir ve YALNIZCA `decision == "block"`
    dalında çağrılır; döndürdüğü metin modele gider, araç yine ÇALIŞMAZ —
    çalışma anı onay anıdır (approvals.decide).

    Bağımlılıklar (`_memory.db`, `_messages`) modül tekilleridir ve ÇAĞRI
    anında okunur, kurulum anında değil: tools._execute_cancel_reminder'daki
    desenin aynısı. Böylece fabrika _init() içinde, tekiller atandıktan sonra
    ama runner kurulurken çağrılabiliyor.

    Üç adım, bu sırayla ve bilinçli olarak FARKLI hata sözleşmeleriyle:

    1. `approvals.request` — BAŞARISIZ OLURSA sink FIRLAR. policy o zaman eski
       kırmızı engel metnine düşer ve araç yine çalışmaz (fail-closed): onay
       kaydı yoksa Kadir'in onaylayabileceği bir şey de yoktur.
    2. transcript kartı — BEST EFFORT. Kart düşmese bile onay kuyrukta duruyor
       ve `GET /api/approvals` ile senkronlanıyor (§4.3); bir Firestore
       hıçkırığı yüzünden çoktan kurulmuş bir onayı çöpe atmak daha kötüdür.
       (run_turn'deki conversations.touch sarmalayıcısıyla aynı sözleşme.)
    3. push — BEST EFFORT, aynı gerekçe. Kart zaten sohbette (§7).

    `actor`/`trust_level` (Task 2) are THREADED from `policy.policy_callback`,
    not recomputed here: the sink reading trust_level back off tool_context
    would be wrong for voice calls, because tool_context.state never carries
    it there (voice_trust.py) -- the callback's already-computed value is the
    only correct source. `cause` is the fixed `approvals.CAUSE_RED_ZONE`:
    every call that reaches this sink is already a red-zone block, by this
    function's own contract above. `operand` -- Ç1's "the ONE concrete thing
    being approved" -- is resolved from a declared table
    (`config.operand_of`, Task 1's TOOL_REVERSIBILITY pattern): undeclared
    tool or an absent argument key both resolve to None rather than a guess.
    `reversible` comes from `tool_name` itself (the actual tool this sink is
    blocking) via `config.is_reversible` -- the tool_grant/agent_grant trap
    (deriving it from "propose_tool" instead of the proposed capability's own
    name) does not apply here, because this sink only ever builds
    kind=tool_call records.
    """

    def sink(tool_name: str, args: dict, tool_context, *,
            actor: str | None = None, trust_level: str | None = None) -> str:
        # ADK'nın public yüzeyi: ReadonlyContext.session -> Session.user_id/.id
        # (kaynaktan doğrulaması app/voice_trust.py'de; tools.get_speaker_status
        # da aynısını kullanır). Beklenmedik bir şekil AttributeError fırlatır
        # ve fail-closed dala düşer -- kimliksiz bir onay kaydı kurmaktansa
        # kırmızı engel metnine düşmek doğrudur.
        session = tool_context.session
        user_id = session.user_id
        session_id = session.id
        db = _memory.db

        # Bu istek için ZATEN bekleyen bir kart varsa ikincisini kurma. Kırmızı
        # engel her turda yeniden tetiklenir ve ajan talimatı "kartı tekrar
        # oluşturma" diye yalnızca RİCA eder; döngüye giren bir model tur başına
        # bir onay dokümanı + bir transcript satırı + bir push üretirdi. Kadir'in
        # vereceği karar zaten aynı karar.
        existing = approvals.find_pending_duplicate(db, user_id, tool_name, args)
        if existing:
            logging.info(
                "approval_sink: aynı istek için kart zaten bekliyor id=%s tool=%s",
                existing, tool_name)
            return APPROVAL_PENDING_REPLY.format(tool_name=tool_name)

        title, detail, card = _approval_card_texts(tool_name, args)
        approval_id = approvals.request(
            db,
            user_id=user_id,
            kind=approvals.KIND_TOOL_CALL,
            title=title,
            detail=detail,
            tool_name=tool_name,
            tool_args=args,
            zone=config.ZONE_RED,
            session_id=session_id,
            actor=actor,
            trust_level=trust_level,
            cause=approvals.CAUSE_RED_ZONE,
            operand=config.operand_of(tool_name, args),
            reversible=config.is_reversible(tool_name),
        )

        try:
            _messages.append(user_id, session_id, "model", card,
                             kind="approval", meta={"approval_id": approval_id})
        except Exception:
            logging.exception(
                "approval_sink: onay kartı transcript'e yazılamadı id=%s -- onay "
                "kuyrukta duruyor, GET /api/approvals ile senkronlanır", approval_id)

        try:
            fcm.send_approval(db, {"id": approval_id, "title": title,
                                   "session_id": session_id})
        except Exception:
            logging.exception(
                "approval_sink: push gönderilemedi id=%s -- kart sohbette duruyor",
                approval_id)

        logging.info("approval_sink: kırmızı engel onaya çevrildi tool=%s id=%s user=%s",
                     tool_name, approval_id, user_id)
        return APPROVAL_PENDING_REPLY.format(tool_name=tool_name)

    return sink


def _init() -> None:
    """Lazy init so tests can import the module without GCP credentials."""
    global _runner, _memory, _audit, _messages, _conversations
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
    # Single audit instance shared by the orchestrator's policy callback AND
    # the guest gate (app/guest_gate.py) -- one trail, one Firestore client.
    _audit = FirestoreAudit(db)
    _messages = messages.MessageStore(db)
    _conversations = conversations.ConversationStore(db)
    _runner = Runner(
        app_name=APP_NAME,
        # approval_sink: kırmızı bölge artık çıkmaz sokak değil (Faz Y3, §5) --
        # engel bir onay kartına döner. guest_gate'e ASLA bağlanmaz (§4.9).
        # zone_resolver: kayıt defterindeki KAZANILMIŞ yeteneklerin bölgesi
        # (Faz Y4, §4.2). Koddaki bölgeyi gevşetemez -- policy.check_zone.
        # extra_toolsets: onaylı MCP sunucuları ajana GERÇEKTEN bağlanır
        # (§6). Bozuk bir kayıt burada sessizce atlanır, runner yine kurulur.
        agent=build_agent(_memory, _audit, model=_build_text_model(),
                          approval_sink=_approval_sink(),
                          zone_resolver=tool_registry.make_zone_resolver(db),
                          extra_toolsets=tool_registry.mcp_toolsets(db)),
        session_service=_session_service,
    )


def _device_token_db():
    """Soğuk başlangıçta kapıyı kendisi açar (final review C1): require_user
    gövdeden ÖNCE koştuğu için tembel _init'e güvenemez. _init idempotenttir;
    sync dependency threadpool'da koşar, ağır init orada güvenlidir."""
    _init()
    return _memory.db


auth.init(_device_token_db)


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
            _memory, FirestoreAudit(db), model=_build_text_model(voice=True),
            # ONLY the voice agent gets a trust provider: identity signals exist
            # only for live voice connections, and this keeps the text runner
            # structurally unable to see them (app/voice_trust.py).
            trust_provider=voice_trust.lookup,
            # The sink, unlike the provider, goes to BOTH runners: a red-zone
            # tool asked for by voice must be approvable too (Faz Y3, §5).
            approval_sink=_approval_sink(),
            # Same registry as the text runner (_memory.db is the shared client,
            # not this module-local `db`): one grant, both runners see it.
            zone_resolver=tool_registry.make_zone_resolver(_memory.db),
            # ...and the same granted MCP servers actually attach here too
            # (§6). Deliberately a SECOND set of toolset instances rather than
            # the text runner's: a toolset owns an MCP session and ADK closes
            # it with its agent, so sharing one across two agents would close
            # it out from under the other.
            extra_toolsets=tool_registry.mcp_toolsets(_memory.db),
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
    # Buraya ulaşıldıysa tur başarılıdır (runner hatası yukarı fırlardı):
    # boş cevap da gerçek bir model sonucudur, sayılır.
    _bump_chat_counter()
    return reply


def _bump_chat_counter() -> None:
    """North Star §4.5: her başarılı chat turu vitals/counters'ta
    chat_turns_today'i artırır. conversations.touch ile aynı "best effort"
    sözleşmesi: sayaç defter tutmaktır, cevap çoktan üretilmiştir — Firestore
    hıçkırığı turu asla bozmaz, loglanıp geçilir. _memory, _init'i no-op'a
    çevirip _messages/_runner'ı doğrudan bağlayan testlerde None kalabilir
    (run_turn'deki _conversations gardının aynısı)."""
    if _memory is None:
        return
    try:
        vitals.bump(_memory.db, "chat_turns_today")
    except Exception:
        logging.exception("run_turn: vitals sayacı yazılamadı -- tura devam ediliyor")


class ChatRequest(BaseModel):
    session_id: str
    message: str


class EnrollRequest(BaseModel):
    clips: list[str]  # base64-encoded PCM16 mono 16kHz utterances
    device_hint: str = "unknown"


class EventRequest(BaseModel):
    """Olay katmanı (§4.4) gövdesi: kim, ne tür, ne taşıyor."""
    source: str
    kind: str
    payload: dict = {}


class FcmRegisterRequest(BaseModel):
    """Android cihazın FCM token kaydı (JarvisFCMService.onNewToken)."""
    token: str


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
async def enroll(req: EnrollRequest, email: str = Depends(require_google_user)):
    if not req.clips:
        raise HTTPException(status_code=400, detail="En az bir ses klibi gerekli")
    db = _enroll_db()
    valid_grant = await asyncio.to_thread(voice_challenge.has_valid_grant, db, email)
    if not valid_grant:
        raise HTTPException(
            status_code=409,
            detail="Ses kaydı için önce sesli doğrulama kodu gereklidir (/api/voice/challenge)",
        )
    try:
        raw_clips = [base64.b64decode(clip) for clip in req.clips]
    except Exception:
        logging.exception("enroll: clip decode failed for user_id=%s", email)
        raise HTTPException(
            status_code=400,
            detail="Ses klibi çözümlenemedi (geçersiz base64)",
        )

    # M3 (cleanup wave 2026-08-11): N clips share ONE CM_TIMEOUT_S budget in the CM
    # gate below (asyncio.wait_for wraps the whole batch) -- an unbounded request
    # doesn't fail loudly, it just eventually 503s once that shared budget runs out.
    # Checked AFTER the base64-decode 400 above (pinned by
    # test_enroll_rejects_malformed_base64_clip) and BEFORE the CM gate, so an
    # oversized/over-count batch never reaches inference.
    if len(raw_clips) > config.ENROLL_MAX_CLIPS:
        raise HTTPException(
            status_code=400,
            detail=f"En fazla {config.ENROLL_MAX_CLIPS} ses klibi gönderilebilir",
        )
    for idx, pcm in enumerate(raw_clips):
        if len(pcm) > config.ENROLL_MAX_CLIP_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{idx + 1}. ses klibi çok büyük "
                    f"(en fazla {config.ENROLL_MAX_CLIP_BYTES} bayt)"
                ),
            )

    # Anchors are immutable and un-evictable (config.CM_ENROLL_REQUIRED
    # docstring), so this gate fails CLOSED: no verdict is not a pass, unlike
    # the live verify path which fails open so Kadir is never locked out
    # mid-conversation. Runs BEFORE the ECAPA embedding below so a rejected
    # clip costs no inference.
    if config.CM_ENABLED and config.CM_ENROLL_REQUIRED:
        # C1b (final review, 2026-08-11): jarvis-brain (3 Gi) deliberately never
        # warms the ~1.2 GiB CM (see this file's _warmup_targets(), gated on
        # config.CM_WARMUP). A misrouted enroll call landing here anyway must
        # not lazy-load the model into a live service under load -- that is
        # exactly the 2026-08-10 OOM class. Refuse fail-closed instead, WITHOUT
        # calling antispoof.is_bonafide (which would trigger the load). On
        # jarvis-voice, CM_WARMUP loads the model at startup, so
        # antispoof.is_loaded() is normally True and this branch is a no-op;
        # a cold-scale-up instance racing a request briefly 503s here, which is
        # the correct fail-closed answer, not a lazy load.
        if not antispoof.is_loaded():
            logging.warning(
                "enroll: CM not loaded in this process, refusing without scoring "
                "user=%s", email,
            )
            raise HTTPException(
                status_code=503,
                detail="Ses doğrulaması şu anda yapılamıyor, birazdan tekrar dene",
            )
        try:
            # Budgeted like the sibling live-verify call (app/voice.py:378-380):
            # a stuck forward pass, or _load_model() blocking on
            # huggingface_hub.snapshot_download when CM_MODEL_DIR is
            # unpopulated, must not hang this request forever. TimeoutError is
            # caught by the same except below -> 503, so fail-closed semantics
            # are unchanged. asyncio cannot cancel the worker thread itself
            # (see antispoof.is_bonafide's logging note), only stop waiting on it.
            verdicts = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: [antispoof.is_bonafide(pcm) for pcm in raw_clips]
                ),
                config.CM_TIMEOUT_S,
            )
        except Exception:
            logging.exception("enroll: CM evaluation failed for %s", email)
            raise HTTPException(
                status_code=503,
                detail="Ses doğrulaması şu anda yapılamıyor, birazdan tekrar dene",
            )
        for idx, (cm_ok, cm_fake_prob) in enumerate(verdicts):
            logging.info(
                "enroll CM: user=%s clip=%d/%d bytes=%d cm_fake_prob=%.4f verdict=%s",
                email, idx + 1, len(verdicts), len(raw_clips[idx]), cm_fake_prob,
                "bonafide" if cm_ok else "SPOOF",
            )
            if not cm_ok:
                raise HTTPException(
                    status_code=422,
                    detail=f"{idx + 1}. ses klibi sahte olarak işaretlendi, kayıt yapılmadı",
                )

    try:
        # OFF THE EVENT LOOP, for the same reason the live verify path is
        # (app/voice.py): speaker.embed is real ECAPA inference plus, on the
        # first call in a process, the ~89 MB lazy model load -- seconds of
        # blocking CPU, multiplied by the number of clips. Run inline in this
        # async endpoint it stalls the whole loop, and jarvis-brain serves
        # /api/chat and /ws/voice from that same loop.
        vecs = await asyncio.to_thread(
            lambda: [speaker.embed(pcm) for pcm in raw_clips]
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


@app.post("/api/jobs/reminders-tick")
async def reminders_tick_job(email: str = Depends(require_scheduler)):
    """Scheduler tick: zamanı gelen hatırlatmaları FCM'e dağıtır (Faz Y2.4).
    repo-watch deseni: asyncio.to_thread + 502 sarması."""
    try:
        _init()
        return await asyncio.to_thread(
            lambda: reminders.dispatch_due(_memory.db, fcm.send_reminder)
        )
    except Exception:
        logging.exception("reminders-tick job: dispatch_due failed")
        raise HTTPException(
            status_code=502,
            detail="Hatırlatma dağıtımı şu an yapılamıyor (altyapı hatası). Az sonra tekrar dene.",
        )


@app.post("/api/fcm/register")
async def fcm_register(req: FcmRegisterRequest, email: str = Depends(require_user)):
    """Android cihazın FCM token'ını kaydeder (Faz Y2.4)."""
    try:
        _init()
        return fcm.register_token(_memory.db, email, req.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("fcm/register: failed for user_id=%s", email)
        raise HTTPException(
            status_code=502,
            detail="Bildirim kaydı şu an yapılamıyor (altyapı hatası). Az sonra tekrar dene.",
        )


class DeviceTokenRequest(BaseModel):
    device: str = Field(min_length=1)

    @field_validator("device")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("device boş olamaz")
        return v


@app.post("/api/device-tokens")
async def mint_device_token(req: DeviceTokenRequest,
                            email: str = Depends(require_google_user)):
    """Kalıcı cihaz token'ı basar (Wear W0, spec §4.2). Yalnız TAZE Google
    oturumuyla: cihaz token'ı kendi soyunu yönetemez. Düz token YALNIZ bu
    cevapta görünür."""
    try:
        _init()
        # Firestore yazımı senkron/blocking -- approvals uçlarındaki desenin
        # aynısı: event loop'u bloklamamak için thread'de koşar (final review
        # Important 2a).
        return await asyncio.to_thread(
            lambda: device_tokens.mint(_memory.db, email=email, device=req.device)
        )
    except Exception:
        logging.exception("device-tokens: mint failed for user_id=%s", email)
        raise HTTPException(
            status_code=502,
            detail="Cihaz token'ı şu an basılamıyor (altyapı hatası). Az sonra tekrar dene.",
        )


@app.get("/api/device-tokens")
async def list_device_tokens(email: str = Depends(require_google_user)):
    try:
        _init()
        tokens = await asyncio.to_thread(lambda: device_tokens.list_tokens(_memory.db))
        return {"tokens": tokens}
    except Exception:
        logging.exception("device-tokens: list failed for user_id=%s", email)
        raise HTTPException(
            status_code=502,
            detail="Cihaz token'ları şu an listelenemiyor (altyapı hatası). Az sonra tekrar dene.",
        )


@app.delete("/api/device-tokens/{token_id}")
async def revoke_device_token(token_id: str,
                              email: str = Depends(require_google_user)):
    try:
        _init()
        message = await asyncio.to_thread(lambda: device_tokens.revoke(_memory.db, token_id))
    except Exception:
        logging.exception("device-tokens: revoke failed id=%s user_id=%s", token_id, email)
        raise HTTPException(
            status_code=502,
            detail="Cihaz token'ı şu an iptal edilemiyor (altyapı hatası). Az sonra tekrar dene.",
        )
    # Bilinmeyen id: approvals uçlarındaki 404 deseninin aynısı -- try/except
    # BLOĞUNUN DIŞINDA, yoksa geniş `except Exception` bunu 502'ye çevirirdi
    # (final review Minor 4).
    if message is None:
        raise HTTPException(status_code=404, detail="Cihaz token'ı bulunamadı")
    return {"ok": True, "message": message}


# --- Onay merkezi (Faz Y3, spec §9) ----------------------------------------
#
# Beş ucun ortak deseni: senkron Firestore işi asyncio.to_thread içinde (repo-watch
# gerekçesinin aynısı -- aynı loop /api/chat ve /ws/voice'u da sunuyor), altyapı
# hatası Türkçe 502. Sahiplik ihlali 404'tür, 403 DEĞİL: başkasının onayının
# varlığı bile sızmamalı (spec §4.4). Bu yüzden 404 HTTPException'ları try
# bloğunun DIŞINDA fırlatılır -- içeride olsalardı geniş `except Exception` onları
# yakalayıp 502'ye çevirirdi.

APPROVAL_NOT_FOUND = "Onay bulunamadı"
APPROVAL_UNAVAILABLE = "Onaylar şu an okunamıyor (altyapı hatası). Az sonra tekrar dene."
APPROVAL_DECISION_UNAVAILABLE = (
    "Onay kararı şu an işlenemiyor (altyapı hatası). Az sonra tekrar dene."
)


@app.get("/api/approvals")
async def list_approvals(email: str = Depends(require_user)):
    """Bekleyen onaylar (süresi geçmişler hariç). Uygulama her açılışta buradan
    senkronlanır — push kaçsa bile onay kaybolmaz (spec §4.3)."""
    try:
        _init()
        items = await asyncio.to_thread(lambda: approvals.list_pending(_memory.db, email))
    except Exception:
        logging.exception("approvals: list failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=APPROVAL_UNAVAILABLE)
    return {"approvals": items}


@app.get("/api/approvals/reasons")
async def list_reject_reasons(email: str = Depends(require_user)):
    """Preset rejection reasons for the client's reject picker (Onay Kartı
    2.0, Task 3, P2c). Registered BEFORE /api/approvals/{approval_id} on
    purpose: route matching is by registration order, and a later
    registration here would let that path-param route swallow "reasons" as
    an approval id."""
    return {"reasons": [
        {"id": r.id, "title": r.title, "prompt_fill": r.prompt_fill}
        for r in approvals.REJECT_REASONS
    ]}


@app.get("/api/approvals/{approval_id}")
async def get_approval(approval_id: str, email: str = Depends(require_user)):
    """Tek onayın GÜNCEL durumu: kart rozetini transcript'e gömmüyoruz, tek
    gerçek kaynak onay dokümanıdır (spec §7)."""
    try:
        _init()
        item = await asyncio.to_thread(lambda: approvals.get(_memory.db, approval_id, email))
    except Exception:
        logging.exception("approvals: get failed id=%s user_id=%s", approval_id, email)
        raise HTTPException(status_code=502, detail=APPROVAL_UNAVAILABLE)
    if item is None:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND)
    return item


# Sentence handed back to the model as the rejected tool call's result (Onay
# Kartı 2.0, Task 3, P2b). See _decide_approval's docstring for WHERE this
# actually reaches the model -- it is not the tool call's own synchronous
# return value, because that value was already produced (and consumed) at
# approval-REQUEST time, long before a decision exists.
REJECTION_MODEL_NOTICE = "Kullanıcı {tool} çağrısını reddetti: {reason}"


async def _decide_approval(approval_id: str, email: str, decision: str,
                           reason: str | None = None) -> dict:
    """approve/reject uçlarının ortak gövdesi. approvals.decide sahiplik
    ihlalini ve olmayan onayı BİREBİR aynı `status="not_found"` ile döner; uç
    ikisini de aynı 404'e çevirir.

    FINDING (Onay Kartı 2.0, Task 3, P2b -- see task-3-report.md for the
    full trace): decide() itself has NO channel back into a live model turn.
    The tool call that raised this approval already returned its own result
    -- APPROVAL_PENDING_REPLY, "onay kartı gönderildi, bekle" -- at REQUEST
    time (_approval_sink, above). By the time a decision lands here, minutes
    or hours later over a completely separate HTTP request, that ADK turn is
    long over; there is no open function-call waiting on this response. The
    only path back into the model's context AT ALL is the same one the
    pending card itself already relies on: _messages -> _ensure_session's
    transcript replay on the session's next cold start (main.py, above,
    "rehydrate"). So on an ACTUAL new rejection (not a stale/duplicate
    `already` touch), this appends the same kind of row the sink itself
    writes -- best effort, exactly like the sink's card/push writes: the
    decision is already durably recorded above regardless of whether this
    write succeeds."""
    try:
        _init()
        out = await asyncio.to_thread(
            lambda: approvals.decide(_memory.db, approval_id, email, decision, reason=reason)
        )
    except Exception:
        logging.exception("approvals: decide failed id=%s user_id=%s decision=%s",
                          approval_id, email, decision)
        raise HTTPException(status_code=502, detail=APPROVAL_DECISION_UNAVAILABLE)
    if out["status"] == approvals.STATUS_NOT_FOUND:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND)

    if (decision == approvals.STATUS_REJECTED
            and out["status"] == approvals.STATUS_REJECTED
            and not out["already"]):
        try:
            approval = await asyncio.to_thread(
                lambda: approvals.get(_memory.db, approval_id, email))
            if approval is not None:
                tool = approval.get("tool_name") or approval.get("kind") or "işlem"
                text = REJECTION_MODEL_NOTICE.format(tool=tool, reason=reason)
                _messages.append(email, approval["session_id"], "model", text,
                                 kind="approval_decision",
                                 meta={"approval_id": approval_id})
        except Exception:
            logging.exception(
                "approvals: ret gerekçesi transcript'e yazılamadı id=%s -- karar "
                "kayıtlı ama model bir sonraki soğuk başlangıca kadar görmeyecek",
                approval_id)
    return out


@app.post("/api/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str, email: str = Depends(require_user)):
    """Onayla ve eylemi ÇALIŞTIR. İkinci çağrı `already: true` döner ve aracı
    yeniden çalıştırmaz (claim dokümanı, spec §4.2)."""
    return await _decide_approval(approval_id, email, approvals.STATUS_APPROVED)


class RejectRequest(BaseModel):
    """Body for POST /api/approvals/{id}/reject (Onay Kartı 2.0, Task 3,
    P2a): a reason is mandatory at the HTTP boundary too, not only inside
    approvals.decide(). `default="", validate_default=True` routes an
    ENTIRELY MISSING field through the SAME strip-and-reject validator as a
    whitespace-only one -- without `validate_default`, pydantic v2 only runs
    field validators on values actually present in the body, so a missing
    field would surface its own generic English "Field required" error
    instead of this constraint's Turkish detail. Free text or a
    REJECT_REASONS preset's `prompt_fill` are both accepted; this model does
    not care which."""
    reason: str = Field(default="", validate_default=True)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("reddetme gerekçesi boş olamaz")
        return v


@app.post("/api/approvals/{approval_id}/reject")
async def reject_approval(approval_id: str, req: RejectRequest,
                          email: str = Depends(require_user)):
    """Reddet: yürütme YOK. Gerekçe zorunlu (Onay Kartı 2.0, Task 3, P2a) --
    bare bir ret modele hiçbir şey öğretmez ve aynı çağrıyı tekrarlatır."""
    return await _decide_approval(approval_id, email, approvals.STATUS_REJECTED,
                                  reason=req.reason)


@app.post("/api/jobs/approvals-tick")
async def approvals_tick_job(email: str = Depends(require_scheduler)):
    """Scheduler turu: süresi geçen bekleyen onayları `expired`'a çeker.

    Bir TEMİZLİK yoludur, güvenlik sınırı DEĞİL — gerçek zaman aşımı garantisi
    approvals.decide()'ın süre kontrolündedir (spec §4.1). Bu iş hiç koşmasa
    bile süresi geçmiş bir onay çalıştırılamaz."""
    try:
        _init()
        return await asyncio.to_thread(lambda: approvals.expire_due(_memory.db))
    except Exception:
        logging.exception("approvals-tick job: expire_due failed")
        raise HTTPException(
            status_code=502,
            detail="Onay turu şu an yapılamıyor (altyapı hatası). Az sonra tekrar dene.",
        )


@app.post("/api/jobs/workspace-poll")
async def workspace_poll_job(email: str = Depends(require_scheduler)):
    """Cloud Scheduler'ın tetiklediği Calendar + Gmail kontrol turu (Faz Y2.2/Y2.3).

    repo-watch deseninin aynası: asyncio.to_thread (senkron Google API +
    Firestore I/O loop'u dondurmasın), 502 sarması. Token yoksa poller
    kendisi handled:false döner (çökmez)."""
    from . import workspace_poll

    try:
        _init()
        return await asyncio.to_thread(workspace_poll.poll_all, _memory.db)
    except Exception:
        logging.exception("workspace-poll job: poll_all failed")
        raise HTTPException(
            status_code=502,
            detail="Workspace kontrolü şu an yapılamıyor (altyapı hatası). Az sonra tekrar dene.",
        )


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


@app.post("/api/jobs/event")
async def event_job(req: EventRequest, email: str = Depends(require_scheduler)):
    """Olay katmanı iskeleti (North Star §4.4): scheduler/Pub-Sub olayını kaydeder
    ve kural motorunun kararını döner. Bilinmeyen kind 400 DEĞİLDİR — gözlem
    olarak kaydedilip handled=False döner (app/events.py docstring)."""
    try:
        _init()
        # asyncio.to_thread, repo-watch'taki gerekçenin aynısı: events.record
        # senkron Firestore + (health_check'te) ağ I/O'su yapar; loop'ta koşarsa
        # /api/chat ve /ws/voice'i de dondurur.
        return await asyncio.to_thread(
            lambda: events.record(
                _memory.db, source=req.source, kind=req.kind, payload=req.payload
            )
        )
    except Exception:
        logging.exception("event job: record failed (source=%s kind=%s)", req.source, req.kind)
        raise HTTPException(
            status_code=502,
            detail="Olay şu an işlenemiyor (altyapı hatası). Az sonra tekrar dene.",
        )


# Misafir Kapısı (North Star §4.9): kimlik-doğrulamalı MCP endpoint'i.
# "/" StaticFiles mount'undan ÖNCE kaydedilmeli, yoksa statik dosya yakalayıcısı
# /mcp isteklerini de yutar.
app.mount("/mcp", guest_gate.asgi, name="guest-gate")


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def _mcp_trailing_slash_redirect():
    """Starlette Mount yalnızca "/mcp/..." ile eşleşir; tam "/mcp" isteği web
    StaticFiles yakalayıcısına düşer (POST'a 405 verir). MCP client'larının
    çoğu slash'siz adresi kullanır — 307 ile "/mcp/"'e taşı (method korunur)."""
    from starlette.responses import RedirectResponse

    return RedirectResponse("/mcp/", status_code=307)


_web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
