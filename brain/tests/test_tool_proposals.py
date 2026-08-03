"""Araç kazanım merdiveni testleri (Faz Y4.1, Görev 2): Jarvis ÖNERİR, Kadir VERİR.

Taşıyıcı pimler — silinirse §8.5'in "yetkilendirme her zaman insanidir"
garantisi kalkar:

- `test_propose_tool_writes_nothing_to_the_registry` (spec §4.1): öneri yalnızca
  bir onay kurar. Kayıt defterine yazan TEK yer onay yürütücüsüdür.
- `test_propose_tool_rejects_red_zone` (spec §4.3): üretilmiş yetenek kırmızıya
  asla; kırmızı bir yetenek koda insan eliyle girer.
- `test_rejected_grant_writes_nothing` / `test_expired_grant_writes_nothing`:
  onay olmadan kayıt olmaz.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import approvals, config, tool_registry, tools
from app.memory import Memory
from tests.fakes import FakeDB

USER = "kadir@example.com"
SESSION_ID = "s1"
NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)

MCP_URL = "https://mcp.example.com/mcp"
MCP_CMD = "npx -y @modelcontextprotocol/server-github"


def _now():
    return NOW.isoformat()


def _at(minutes):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def _ctx(user_id=USER, session_id=SESSION_ID):
    """tools.propose_tool yalnızca tool_context.session.{user_id,id} okur —
    tests/test_tools.py'deki aynı duck-type yeterli."""
    return SimpleNamespace(session=SimpleNamespace(user_id=user_id, id=session_id))


def _db():
    db = FakeDB()
    tools.init(Memory(db))
    return db


def _propose(db=None, *, name="github_mcp", kind=tool_registry.KIND_MCP,
             zone=config.ZONE_YELLOW, why="GitHub PR'larını okuyabilmem için gerekiyor.",
             mcp_url=MCP_URL, mcp_command="", scopes="repo,read:org", ctx=None):
    return tools.propose_tool(name, kind, zone, why, ctx or _ctx(),
                              mcp_url=mcp_url, mcp_command=mcp_command, scopes=scopes)


def _approvals(db):
    return list(db.collection(approvals.COLLECTION).docs.items())


def _registry(db):
    return db.collection(tool_registry.COLLECTION).docs


def _only_approval(db):
    (aid, doc), = _approvals(db)
    return aid, doc


# ---------------------------------------------------------------------------
# propose_tool — öneri kurar, kayıt YAZMAZ
# ---------------------------------------------------------------------------


def test_propose_tool_writes_nothing_to_the_registry():
    """TAŞIYICI PİM (spec §4.1). Öneri bir onay kurar; kayıt defteri BOŞ kalır.
    Bu pim düşerse Jarvis kendi kendine yetki verebiliyor demektir."""
    db = _db()
    out = _propose(db)
    aid, doc = _only_approval(db)
    assert doc["kind"] == approvals.KIND_TOOL_GRANT
    assert doc["status"] == approvals.STATUS_PENDING
    assert doc["user_id"] == USER
    assert doc["session_id"] == SESSION_ID
    assert doc["tool_name"] == "github_mcp"
    assert doc["tool_args"]["zone"] == config.ZONE_YELLOW
    assert doc["tool_args"]["kind"] == tool_registry.KIND_MCP
    assert doc["tool_args"]["mcp_url"] == MCP_URL
    assert doc["tool_args"]["approval_id"] == aid   # izlenebilirlik (spec §3)
    assert "GitHub PR" in doc["detail"]              # gerekçe kartta görünür
    assert _registry(db) == {}
    assert "onay" in out.lower()


def test_propose_tool_rejects_red_zone():
    """TAŞIYICI PİM (spec §4.3): kırmızı istenemez — kart bile oluşmaz."""
    db = _db()
    out = _propose(db, zone=config.ZONE_RED)
    assert _approvals(db) == []
    assert _registry(db) == {}
    assert "kırmızı" in out.lower()


def test_propose_tool_rejects_unknown_kind():
    db = _db()
    out = _propose(db, kind="wasm")
    assert _approvals(db) == []
    assert "wasm" in out


def test_propose_tool_rejects_empty_name():
    db = _db()
    out = _propose(db, name="   ")
    assert _approvals(db) == []
    assert "ad" in out.lower()


def test_propose_tool_requires_a_transport_for_mcp():
    db = _db()
    out = _propose(db, mcp_url="", mcp_command="")
    assert _approvals(db) == []
    assert "mcp" in out.lower()


def test_propose_tool_accepts_a_stdio_command_as_transport():
    db = _db()
    _propose(db, mcp_url="", mcp_command=MCP_CMD)
    _, doc = _only_approval(db)
    assert doc["tool_args"]["mcp_command"] == MCP_CMD


def test_propose_tool_does_not_need_a_transport_for_builtin():
    db = _db()
    _propose(db, name="draw_diagram", kind=tool_registry.KIND_BUILTIN,
             zone=config.ZONE_GREEN, mcp_url="", mcp_command="")
    _, doc = _only_approval(db)
    assert doc["tool_args"]["kind"] == tool_registry.KIND_BUILTIN


def test_propose_tool_does_not_create_a_second_card_for_a_pending_name():
    db = _db()
    _propose(db)
    out = _propose(db, why="ikinci kez soruyorum")
    assert len(_approvals(db)) == 1
    assert "bekleyen bir öneri" in out.lower()


def test_propose_tool_still_cards_a_different_name():
    db = _db()
    _propose(db, name="github_mcp")
    _propose(db, name="notion_mcp")
    assert len(_approvals(db)) == 2


def test_propose_tool_refuses_an_already_granted_name():
    db = _db()
    tool_registry.grant(db, name="github_mcp", kind=tool_registry.KIND_MCP,
                        zone=config.ZONE_YELLOW, why="önceden verildi",
                        approval_id="a0", mcp={"transport": "http", "url": MCP_URL})
    out = _propose(db)
    assert _approvals(db) == []
    assert "zaten" in out.lower()


def test_propose_tool_allows_reproposal_after_revoke():
    """§4.4: iptal edilen bir ad yeniden önerilebilir."""
    db = _db()
    tool_registry.grant(db, name="github_mcp", kind=tool_registry.KIND_MCP,
                        zone=config.ZONE_YELLOW, why="önceden verildi",
                        approval_id="a0", mcp={"transport": "http", "url": MCP_URL})
    tool_registry.revoke(db, "github_mcp")
    _propose(db)
    assert len(_approvals(db)) == 1


def test_propose_tool_returns_an_observation_when_the_backend_fails():
    """İlke 4: araç fırlatmaz, gözlem döner."""

    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    tools.init(Memory(BoomDB()))
    out = _propose()
    assert isinstance(out, str) and "öner" in out.lower()


def test_propose_tool_is_green_and_registered_as_a_tool():
    """Öneri kurmak zararsızdır — asıl karar onay kartındadır (spec §5)."""
    assert config.TOOL_ZONES["propose_tool"] == config.ZONE_GREEN
    assert tools.propose_tool in tools.ALL_TOOLS


# ---------------------------------------------------------------------------
# tool_grant onayı — kayıt defterine YALNIZCA onay yazar
# ---------------------------------------------------------------------------


def test_approved_grant_writes_the_registry():
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED)
    assert out["status"] == approvals.STATUS_APPROVED
    # §4.5: çalışan ajanın araç listesi yerinde değiştirilemez — İDDİA ETMİYORUZ.
    assert "bir sonraki açılışta" in out["outcome"].lower()
    doc = _registry(db)["github_mcp"]
    assert doc["status"] == tool_registry.STATUS_GRANTED
    assert doc["zone"] == config.ZONE_YELLOW
    assert doc["kind"] == tool_registry.KIND_MCP
    assert doc["approval_id"] == aid
    assert doc["mcp"]["url"] == MCP_URL
    assert doc["mcp"]["scopes"] == ["repo", "read:org"]


def test_approved_stdio_grant_keeps_command_and_args():
    db = _db()
    _propose(db, mcp_url="", mcp_command=MCP_CMD, scopes="")
    aid, _ = _only_approval(db)
    approvals.decide(db, aid, USER, approvals.STATUS_APPROVED)
    mcp = _registry(db)["github_mcp"]["mcp"]
    assert mcp["transport"] == "stdio"
    assert mcp["command"] == "npx"
    assert mcp["args"] == ["-y", "@modelcontextprotocol/server-github"]


def test_rejected_grant_writes_nothing():
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    out = approvals.decide(db, aid, USER, approvals.STATUS_REJECTED)
    assert out["status"] == approvals.STATUS_REJECTED
    assert _registry(db) == {}


def test_expired_grant_writes_nothing():
    """§4.1 (Y3): zaman aşımı KARAR anında uygulanır — süresi geçmiş bir öneri
    onaylanamaz, dolayısıyla kayıt da yazılmaz."""
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED,
                           now_fn=lambda: _at(config.APPROVAL_TTL_MINUTES + 1))
    assert out["status"] == approvals.STATUS_EXPIRED
    assert _registry(db) == {}


def test_grant_by_someone_else_writes_nothing():
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    out = approvals.decide(db, aid, "baskasi@example.com", approvals.STATUS_APPROVED)
    assert out["status"] == approvals.STATUS_NOT_FOUND
    assert _registry(db) == {}


def test_grant_without_a_registered_executor_fails_closed():
    """Allowlist (Y3 §6) tool_grant için de geçerli: yürütücü yoksa kayıt YOK."""
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED, executors={})
    assert out["status"] == approvals.STATUS_FAILED
    assert _registry(db) == {}


def test_approving_twice_grants_only_once():
    """Y3 §4.2 idempotanslığı tool_grant'te de geçerli: ikinci dokunuş kaydı
    yeniden yazmaz (approval_id ilk kararınki kalır)."""
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    approvals.decide(db, aid, USER, approvals.STATUS_APPROVED)
    again = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED)
    assert again["already"] is True
    assert _registry(db)["github_mcp"]["approval_id"] == aid


def test_a_red_zone_grant_doc_is_refused_by_the_executor():
    """Derinlikli savunma: onay dokümanı elle kırmızıya çevrilse bile yürütücü
    yazmaz (§4.3 iki katmanda uygulanır)."""
    db = _db()
    _propose(db)
    aid, _ = _only_approval(db)
    db.collection(approvals.COLLECTION).document(aid).set(
        {"tool_args": {"name": "github_mcp", "kind": tool_registry.KIND_MCP,
                       "zone": config.ZONE_RED, "why": "x", "approval_id": aid,
                       "mcp_url": MCP_URL, "mcp_command": "", "scopes": ""}}, merge=True)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED)
    assert _registry(db) == {}
    assert "kırmızı" in (out["outcome"] or "").lower()


# ---------------------------------------------------------------------------
# approvals.decide — kind'a göre yürütücü seçimi (mevcut tool_call DEĞİŞMEDİ)
# ---------------------------------------------------------------------------


def test_tool_call_dispatch_is_unchanged():
    """Regresyon pimi: tool_call hâlâ tool_name'e göre yürütücü seçer."""
    db = FakeDB()
    calls = []
    aid = approvals.request(db, user_id=USER, kind=approvals.KIND_TOOL_CALL,
                            title="t", detail="d", tool_name="cancel_reminder",
                            tool_args={"reminder_id": "r1"}, zone=config.ZONE_RED,
                            session_id=SESSION_ID)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED,
                           executors={"cancel_reminder": lambda a, u: calls.append((a, u)) or "ok"})
    assert out["outcome"] == "ok"
    assert calls == [({"reminder_id": "r1"}, USER)]


def test_unknown_kind_is_approved_but_never_executed():
    """Y3 davranışı korunur: yürütülebilir olmayan bir kind onaylanır, yan
    etkisi olmaz."""
    db = FakeDB()
    aid = approvals.request(db, user_id=USER, kind="agent_spec", title="t", detail="d",
                            zone=config.ZONE_YELLOW, session_id=SESSION_ID)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED,
                           executors={approvals.EXECUTOR_TOOL_GRANT: lambda a, u: "OLMAMALI"})
    assert out["status"] == approvals.STATUS_APPROVED
    assert out["outcome"] is None


def test_a_tool_call_cannot_borrow_the_grant_executor():
    """İsim uzayı sızdırmaz: `kind:` ön ekli bir yürütücü anahtarı bir ARAÇ
    adıyla ele geçirilemez."""
    db = FakeDB()
    aid = approvals.request(db, user_id=USER, kind=approvals.KIND_TOOL_CALL,
                            title="t", detail="d", tool_name=approvals.EXECUTOR_TOOL_GRANT,
                            tool_args={"name": "evil", "kind": "builtin", "zone": "green"},
                            zone=config.ZONE_RED, session_id=SESSION_ID)
    out = approvals.decide(db, aid, USER, approvals.STATUS_APPROVED,
                           executors={approvals.EXECUTOR_TOOL_GRANT: lambda a, u: "ELE GEÇİRİLDİ"})
    assert out["status"] == approvals.STATUS_FAILED
    assert out["outcome"] != "ELE GEÇİRİLDİ"


# ---------------------------------------------------------------------------
# uçtan uca: öneri -> onay -> bölge
# ---------------------------------------------------------------------------


def test_granted_tool_gets_its_zone_end_to_end():
    from app import policy

    db = _db()
    _propose(db, zone=config.ZONE_YELLOW)
    aid, _ = _only_approval(db)
    resolver = tool_registry.make_zone_resolver(db)
    assert policy.check_zone("github_mcp", resolver) == config.ZONE_RED   # onay öncesi
    approvals.decide(db, aid, USER, approvals.STATUS_APPROVED)
    assert policy.check_zone("github_mcp", resolver) == config.ZONE_YELLOW
