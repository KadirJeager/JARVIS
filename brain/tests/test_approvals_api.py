"""Faz Y3, Görev 5: onay uçları (spec §9) + kırmızı engeli karta çeviren sink.

Gerçek Firestore/ağ YOK: `main._init` no-op'a, `_memory`/`_messages` FakeDB'ye
bağlanır (test_api.py deseni); scheduler ucu test_repo_watch_api.py'deki OIDC
monkeypatch desenini kullanır.

Üç pim taşıyıcıdır:
- `test_approve_someone_elses_approval_returns_404` — 403 DEĞİL: başkasının
  onayının VARLIĞI bile sızmamalı (spec §4.4).
- `test_sink_writes_exactly_one_chat_row_when_there_are_no_fcm_tokens` — sink
  kartı zaten sohbete yazıyor; FCM fallback'i de yazarsa Kadir aynı onayı İKİ
  satır olarak görür.
- `test_production_runners_get_the_sink` — sink yalnızca metin + ses runner'ına
  bağlanır. Misafir tarafının pimi burada DEĞİL, `tests/test_guest_gate.py`
  içindeki `test_red_zone_call_never_creates_an_approval`'dadır (§4.9).
"""
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

import app.auth as auth_mod
import app.main as main_mod
from app import approvals, config, fcm, messages, policy, trust
from app.agent import build_agent
from app.auth import require_user
from app.memory import Memory
from app.messages import MessageStore
from tests.fakes import FakeDB

USER = "owner@example.com"
OTHER = "baskasi@example.com"
SESSION = "web-2026-08-03"
SA = "approvals-scheduler@proj.iam.gserviceaccount.com"
AUD = "https://jarvis-brain-xyz.run.app"


def _request(db, *, user_id=USER, title="Hatırlatma silinecek", tool_args=None,
             ttl_minutes=60, now_fn=None):
    kwargs = {"now_fn": now_fn} if now_fn else {}
    return approvals.request(
        db,
        user_id=user_id,
        kind=approvals.KIND_TOOL_CALL,
        title=title,
        detail="'su iç' hatırlatması silinecek — kırmızı bölge.",
        tool_name="cancel_reminder",
        tool_args={"reminder_id": "r1"} if tool_args is None else tool_args,
        zone=config.ZONE_RED,
        session_id=SESSION,
        ttl_minutes=ttl_minutes,
        **kwargs,
    )


def _boom(*args, **kwargs):
    raise RuntimeError("firestore down")


def _chat_rows(db):
    return [s.to_dict() for s in db.collection(messages.COLLECTION).stream()]


def _approval_docs(db):
    return list(db.collection(approvals.COLLECTION).docs.values())


class CountingExecutor:
    def __init__(self, result="Hatırlatma iptal edildi."):
        self.calls = []
        self._result = result

    def __call__(self, tool_args, user_id):
        self.calls.append((tool_args, user_id))
        return self._result


class _NullAudit:
    """`policy.make_policy_callback`'in gerektirdiği AuditWriter'ın en ufak
    hâli -- Görev 2'nin sink testi audit_log'un İÇERİĞİYLE değil, o içeriğin
    onay kaydına da ULAŞMASIYLA ilgileniyor."""

    def write(self, entry):
        pass


@pytest.fixture()
def db():
    return FakeDB()


@pytest.fixture()
def client(monkeypatch, db):
    """Auth'u geçen, FakeDB'ye bağlı hazır client."""
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", MessageStore(db))
    main_mod.app.dependency_overrides[require_user] = lambda: USER
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("get", "/api/approvals"),
    ("get", "/api/approvals/a1"),
    ("post", "/api/approvals/a1/approve"),
    ("post", "/api/approvals/a1/reject"),
])
def test_user_endpoints_require_auth(method, path):
    with TestClient(main_mod.app) as c:
        assert getattr(c, method)(path).status_code in (401, 403)


@pytest.fixture()
def scheduler(monkeypatch):
    monkeypatch.setattr(config, "SCHEDULER_SA", SA)
    monkeypatch.setattr(config, "SCHEDULER_AUD", AUD)


def _tick(c, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post("/api/jobs/approvals-tick", headers=headers)


def test_tick_without_token_returns_401(scheduler):
    with TestClient(main_mod.app) as c:
        assert _tick(c).status_code == 401


def test_tick_with_wrong_service_account_returns_403(scheduler, monkeypatch):
    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token",
                        lambda token, request, audience: {"email": "attacker@evil.com"})
    with TestClient(main_mod.app) as c:
        assert _tick(c, "sometoken").status_code == 403


def test_tick_expires_due_approvals(scheduler, monkeypatch, db):
    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token",
                        lambda token, request, audience: {"email": SA})
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    dolan = _request(db, ttl_minutes=-5)      # zaten dolmuş
    duran = _request(db, ttl_minutes=600)

    with TestClient(main_mod.app) as c:
        r = _tick(c, "good-token")

    assert r.status_code == 200
    assert r.json()["expired"] == 1
    docs = db.collection(approvals.COLLECTION).docs
    assert docs[dolan]["status"] == approvals.STATUS_EXPIRED
    assert docs[duran]["status"] == approvals.STATUS_PENDING


def test_tick_returns_502_on_infrastructure_failure(scheduler, monkeypatch, db):
    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token",
                        lambda token, request, audience: {"email": SA})
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(approvals, "expire_due", _boom)

    with TestClient(main_mod.app) as c:
        r = _tick(c, "good-token")

    assert r.status_code == 502 and "altyapı" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/approvals, GET /api/approvals/{id}
# ---------------------------------------------------------------------------


def test_list_returns_the_callers_pending_approvals(client, db):
    mine = _request(db, title="benim onayım")
    _request(db, user_id=OTHER, title="başkasının onayı")

    r = client.get("/api/approvals")

    assert r.status_code == 200
    items = r.json()["approvals"]
    assert [i["id"] for i in items] == [mine]
    assert items[0]["title"] == "benim onayım"
    assert items[0]["status"] == approvals.STATUS_PENDING


def test_list_returns_502_on_infrastructure_failure(client, monkeypatch):
    monkeypatch.setattr(approvals, "list_pending", _boom)
    r = client.get("/api/approvals")
    assert r.status_code == 502 and "altyapı" in r.json()["detail"]


def test_get_returns_the_current_status_of_one_approval(client, db):
    approval_id = _request(db)
    r = client.get(f"/api/approvals/{approval_id}")
    assert r.status_code == 200
    assert r.json()["id"] == approval_id
    assert r.json()["status"] == approvals.STATUS_PENDING
    assert r.json()["tool_name"] == "cancel_reminder"


def test_get_someone_elses_approval_is_404_and_leaks_nothing(client, db):
    """403 DEĞİL 404 (spec §4.4): başkasının onayının varlığı bile sızmamalı —
    cevap, hiç var olmayan bir id'ninkiyle BİREBİR aynı."""
    approval_id = _request(db, user_id=OTHER)

    baskasinin = client.get(f"/api/approvals/{approval_id}")
    olmayan = client.get("/api/approvals/yok-boyle-bir-id")

    assert baskasinin.status_code == 404
    assert baskasinin.json() == olmayan.json()


def test_get_returns_502_on_infrastructure_failure(client, monkeypatch):
    monkeypatch.setattr(approvals, "get", _boom)
    r = client.get("/api/approvals/a1")
    assert r.status_code == 502 and "altyapı" in r.json()["detail"]


# ---------------------------------------------------------------------------
# POST approve / reject
# ---------------------------------------------------------------------------


def test_approve_executes_and_returns_status_and_outcome(client, db, monkeypatch):
    ex = CountingExecutor()
    monkeypatch.setitem(approvals.EXECUTORS, "cancel_reminder", ex)
    approval_id = _request(db)

    r = client.post(f"/api/approvals/{approval_id}/approve")

    assert r.status_code == 200
    assert r.json()["status"] == approvals.STATUS_APPROVED
    assert r.json()["outcome"] == "Hatırlatma iptal edildi."
    assert r.json()["already"] is False
    assert ex.calls == [({"reminder_id": "r1"}, USER)]


def test_approving_twice_reports_already_and_executes_once(client, db, monkeypatch):
    ex = CountingExecutor()
    monkeypatch.setitem(approvals.EXECUTORS, "cancel_reminder", ex)
    approval_id = _request(db)

    first = client.post(f"/api/approvals/{approval_id}/approve")
    second = client.post(f"/api/approvals/{approval_id}/approve")

    assert first.json()["already"] is False
    assert second.status_code == 200 and second.json()["already"] is True
    assert len(ex.calls) == 1


def test_approve_someone_elses_approval_returns_404(client, db, monkeypatch):
    """TAŞIYICI PİM: sahiplik ihlali 403 DEĞİL 404 döner ve eylem çalışmaz."""
    ex = CountingExecutor()
    monkeypatch.setitem(approvals.EXECUTORS, "cancel_reminder", ex)
    approval_id = _request(db, user_id=OTHER)

    r = client.post(f"/api/approvals/{approval_id}/approve")

    assert r.status_code == 404
    assert ex.calls == []
    assert db.collection(approvals.COLLECTION).docs[approval_id]["status"] == \
        approvals.STATUS_PENDING


def test_approve_unknown_approval_returns_404(client):
    assert client.post("/api/approvals/yok-boyle-bir-id/approve").status_code == 404


def test_reject_records_the_decision_without_executing(client, db, monkeypatch):
    ex = CountingExecutor()
    monkeypatch.setitem(approvals.EXECUTORS, "cancel_reminder", ex)
    approval_id = _request(db)

    r = client.post(f"/api/approvals/{approval_id}/reject")

    assert r.status_code == 200 and r.json()["status"] == approvals.STATUS_REJECTED
    assert ex.calls == []
    assert db.collection(approvals.COLLECTION).docs[approval_id]["decided_by"] == USER


def test_reject_someone_elses_approval_returns_404(client, db):
    approval_id = _request(db, user_id=OTHER)
    assert client.post(f"/api/approvals/{approval_id}/reject").status_code == 404


def test_approve_returns_502_on_infrastructure_failure(client, monkeypatch):
    monkeypatch.setattr(approvals, "decide", _boom)
    r = client.post("/api/approvals/a1/approve")
    assert r.status_code == 502 and "altyapı" in r.json()["detail"]


# ---------------------------------------------------------------------------
# _approval_sink() — kırmızı engel -> onay + kart + push
# ---------------------------------------------------------------------------


def _ctx(user_id=USER, session_id=SESSION):
    """ADK ToolContext'in sink'in kullandığı public yüzeyi (ReadonlyContext
    .session -> Session.user_id/.id; bkz. app/voice_trust.py)."""
    return SimpleNamespace(session=SimpleNamespace(user_id=user_id, id=session_id))


@pytest.fixture()
def wired(monkeypatch, db):
    """Sink'i doğrudan çağırabilmek için _memory/_messages bağlanır; FCM
    enjekte edilen sahteye gider."""
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", MessageStore(db))
    sent = []
    monkeypatch.setattr(fcm, "send_approval",
                        lambda db_, approval, **kw: sent.append(approval) or {"ok": True})
    return main_mod._approval_sink(), db, sent


def test_sink_creates_an_approval_a_chat_card_and_a_push(wired):
    sink, db, sent = wired

    text = sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    docs = _approval_docs(db)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["status"] == approvals.STATUS_PENDING
    assert doc["kind"] == approvals.KIND_TOOL_CALL
    assert doc["zone"] == config.ZONE_RED
    assert doc["user_id"] == USER and doc["session_id"] == SESSION
    assert doc["tool_name"] == "cancel_reminder"
    assert doc["tool_args"] == {"reminder_id": "r1"}
    # Modele giden metin: "kart gönderildi, bekle" (agent.py talimatıyla uyumlu).
    assert "onay" in text.lower()
    # Push da gitti ve onay id'sini taşıyor.
    approval_id = list(db.collection(approvals.COLLECTION).docs)[0]
    assert len(sent) == 1 and sent[0]["id"] == approval_id


def test_sink_reuses_a_pending_card_instead_of_minting_a_second(wired):
    """Kırmızı engel HER TURDA tetiklenir. agent.py talimatı "kartı tekrar
    oluşturma" diye yalnızca rica eder; döngüye giren bir model tur başına bir
    onay dokümanı, bir transcript satırı ve bir push üretirdi."""
    sink, db, sent = wired

    first = sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())
    second = sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    assert len(_approval_docs(db)) == 1, "ikinci kart kurulmamalı"
    assert len(sent) == 1, "ikinci push gitmemeli"
    rows = [m for m in MessageStore(db).history(USER, SESSION) if m.get("kind") == "approval"]
    assert len(rows) == 1, "ikinci transcript satırı yazılmamalı"
    assert first == second, "model her iki turda da aynı 'bekle' cevabını almalı"


def test_sink_mints_a_new_card_for_a_different_argument(wired):
    sink, db, _sent = wired

    sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())
    sink("cancel_reminder", {"reminder_id": "r2"}, _ctx())

    assert len(_approval_docs(db)) == 2


def test_sink_card_is_a_model_row_carrying_the_approval_id(wired):
    sink, db, _sent = wired

    sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    approval_id = list(db.collection(approvals.COLLECTION).docs)[0]
    rows = _chat_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "model"
    assert row["kind"] == "approval"
    assert row["meta"] == {"approval_id": approval_id}
    assert row["user_id"] == USER and row["session_id"] == SESSION
    assert "cancel_reminder" in row["text"]


def test_sink_raises_when_the_approval_itself_cannot_be_created(wired, monkeypatch):
    """FAIL-CLOSED: 1. adım başarısızsa sink FIRLAR ve politika eski kırmızı
    metne düşer (app/policy.py). Onay kaydı yoksa kart da push da olmamalı."""
    sink, db, sent = wired
    monkeypatch.setattr(approvals, "request", _boom)

    with pytest.raises(RuntimeError):
        sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    assert _chat_rows(db) == [] and sent == []


def test_sink_raises_when_the_tool_context_has_no_session(wired):
    """Beklenmedik ADK şekli de fail-closed'dır: kimliksiz bir onay kaydı
    kurmaktansa kırmızı engel metnine düşmek doğrudur."""
    sink, _db, _sent = wired
    with pytest.raises(Exception):
        sink("cancel_reminder", {}, SimpleNamespace())


def test_sink_survives_a_transcript_write_failure(wired, monkeypatch, caplog):
    """BEST EFFORT: kart düşmese bile onay KUYRUKTA duruyor ve
    GET /api/approvals ile senkronlanıyor (§4.3) — sink fırlatmaz."""
    sink, db, sent = wired

    class BoomStore:
        def append(self, *a, **kw):
            raise RuntimeError("firestore down")

    monkeypatch.setattr(main_mod, "_messages", BoomStore())

    with caplog.at_level("ERROR"):
        text = sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    assert "onay" in text.lower()
    assert len(_approval_docs(db)) == 1
    assert len(sent) == 1          # kart düşmedi ama push yine gitti
    assert "kart" in caplog.text.lower()


def test_sink_survives_a_push_failure(wired, monkeypatch, caplog):
    sink, db, _sent = wired
    monkeypatch.setattr(fcm, "send_approval", _boom)

    with caplog.at_level("ERROR"):
        text = sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    assert "onay" in text.lower()
    assert len(_approval_docs(db)) == 1
    assert len(_chat_rows(db)) == 1


def test_sink_writes_exactly_one_chat_row_when_there_are_no_fcm_tokens(monkeypatch, db):
    """TAŞIYICI PİM (çift satır tuzağı): cihaz token'ı yokken fcm.dispatch
    bildirimi sohbete düşürür. Sink kartı ZATEN aynı oturuma yazdığı için bu
    Kadir'e aynı onayı İKİ satır olarak gösterirdi. `send_approval` bu yüzden
    fallback_text=None ile çağırır: "çağıran zaten yazdı, düşürme"."""
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", MessageStore(db))
    # fcm SAHTE DEĞİL: gerçek dispatch yolu koşsun, token koleksiyonu boş.
    sink = main_mod._approval_sink()

    sink("cancel_reminder", {"reminder_id": "r1"}, _ctx())

    rows = _chat_rows(db)
    assert len(rows) == 1, f"onay sohbete {len(rows)} satır düşürdü, 1 olmalı"
    assert rows[0]["kind"] == "approval"


def test_sink_populates_context_from_the_policy_callback(wired):
    """Görev 2 (Onay Kartı 2.0), UÇTAN UCA nokta: bir kırmızı bölge engeli --
    sink'i DOĞRUDAN değil, GERÇEK policy.make_policy_callback üzerinden
    tetiklenerek -- kimin istediğini, neden istendiğini ve geri alınıp
    alınamayacağını ZATEN bilen bir onay üretmeli. Bu bağlam bugüne kadar
    policy.write_audit içinde hesaplanıp audit_log'a yazılıyor, sink'e hiç
    ulaşmadan düşüyordu (app/policy.py:59-97, app/main.py:186-220)."""
    sink, db, _sent = wired
    cb = policy.make_policy_callback(_NullAudit(), approval_sink=sink)
    tool_context = SimpleNamespace(
        session=SimpleNamespace(user_id=USER, id=SESSION),
        state={config.TRUST_STATE_KEY: trust.MEDIUM})

    # "send_message" config.TOOL_ZONES/TOOL_REVERSIBILITY'de yok -> zone
    # fail-closed RED'e düşer (decision="block") ve reversible fail-closed
    # False'a düşer -- tam olarak Task 1'in raporundaki senaryo.
    cb(SimpleNamespace(name="send_message"), {"to": "Ayşe"}, tool_context)

    approval_id = list(db.collection(approvals.COLLECTION).docs)[0]
    doc = approvals.get(db, approval_id, USER)
    assert doc["actor"]  # non-empty (policy.ACTOR_ORCHESTRATOR varsayılanı)
    assert doc["trust_level"] == trust.MEDIUM
    assert doc["cause"] == "red_zone"
    assert doc["reversible"] is False  # send_message


# ---------------------------------------------------------------------------
# Üretim bağlantısı: sink metin + ses runner'ında, misafir kapısında DEĞİL
# ---------------------------------------------------------------------------


class _Tool:
    def __init__(self, name):
        self.name = name


@pytest.fixture()
def production_init(monkeypatch):
    """main._init()/_init_voice()'u GERÇEKTEN koşturur; yalnızca dış bağımlılıklar
    (Firestore, e5 fabrikası, model çözümleyicileri) sahtelenir.
    tests/test_trust_propagation.py'deki aynı desen."""
    import app.agent as agent_mod
    import app.memory as memory_mod
    from google.cloud import firestore

    db = FakeDB()
    monkeypatch.setattr(firestore, "Client", lambda *a, **k: db)
    monkeypatch.setattr(memory_mod, "make_e5_embedders", lambda: types.SimpleNamespace(
        embed_passage=lambda text: [0.0], embed_query=lambda text: [0.0]))
    monkeypatch.setattr(main_mod.config, "LLM_BASE_URL", "")
    monkeypatch.setattr(main_mod.config, "resolve_text_model", lambda: "fake-text-model")
    monkeypatch.setattr(main_mod.config, "resolve_voice_model", lambda: "fake-voice-model")

    calls = []

    real_build_agent = agent_mod.build_agent

    def capturing(*args, **kwargs):
        calls.append((args, kwargs))
        return real_build_agent(*args, **kwargs)

    monkeypatch.setattr(agent_mod, "build_agent", capturing)
    for name in ("_runner", "_voice_runner", "_memory", "_messages", "_speaker_service"):
        monkeypatch.setattr(main_mod, name, None)
    monkeypatch.setattr(main_mod, "_session_service", InMemorySessionService())
    return main_mod, calls, db


def _bound_args(call):
    """Kaydedilen çağrıyı build_agent'ın GERÇEK imzasına bağlar (varsayılanlar
    uygulanır), böylece iddia argüman konumlu geçilse de geçerli kalır.
    `build_agent` yukarıda modül yüklenirken import edildi, yani fixture'ın
    monkeypatch'inden önceki asıl fonksiyondur."""
    import inspect

    args, kwargs = call
    bound = inspect.signature(build_agent).bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


def test_production_runners_get_the_sink(production_init):
    """Son mil: `approval_sink=` anahtarını main._init/_init_voice'tan silmek
    kırmızı bölgeyi Y3 öncesindeki çıkmaz sokağa geri döndürür — bu test o
    silmeyi yakalar (ikisini de, ayrı ayrı)."""
    main_module, calls, _db = production_init

    main_module._init_voice()      # önce _init(), sonra ses runner'ı

    assert len(calls) == 2, f"iki build_agent çağrısı bekleniyordu, {len(calls)} geldi"
    text_call, voice_call = (_bound_args(c) for c in calls)
    for label, call in (("METİN", text_call), ("SES", voice_call)):
        assert callable(call["approval_sink"]), (
            f"{label} runner'ı approval_sink olmadan kuruldu: kırmızı bölge yine "
            "çıkmaz sokak")


def test_a_red_tool_call_through_the_production_agent_queues_one_card(production_init):
    """Davranışsal ikiz: gerçek ADK ToolContext'iyle üretim agent'ının kırmızı
    bir aracı, tam olarak BİR onay ve BİR kart üretir; araç çalışmaz."""
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions.session import Session
    from google.adk.tools.tool_context import ToolContext

    main_module, _calls, db = production_init
    main_module._init()

    session = Session(id=SESSION, app_name=main_module.APP_NAME, user_id=USER)
    tool_context = ToolContext(InvocationContext(
        session_service=main_module._session_service, invocation_id="i",
        agent=main_module._runner.agent, session=session,
    ))

    result = main_module._runner.agent.before_tool_callback(
        _Tool("cancel_reminder"), {"reminder_id": "r1"}, tool_context)

    # None DEĞİL => ADK aracı çalıştırmaz.
    assert result is not None and "onay" in result["result"].lower()
    docs = _approval_docs(db)
    assert len(docs) == 1 and docs[0]["tool_name"] == "cancel_reminder"
    assert docs[0]["user_id"] == USER and docs[0]["session_id"] == SESSION
    rows = _chat_rows(db)
    assert len(rows) == 1 and rows[0]["kind"] == "approval"
