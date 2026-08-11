"""Fabrika Kademe 2 — propose_agent (spec §4-§5): Jarvis ÖNERİR, Kadir VERİR.

Taşıyıcı pimler (test_tool_proposals ile aynı sınıf):
- test_propose_agent_writes_nothing_to_the_registry: öneri yalnız kart kurar.
- test_rejected_agent_grant_writes_nothing: onay olmadan kayıt olmaz.
- decide(approved) yolu ÜRETİMİN GERÇEK DİZİSİDİR (tek olaylı kurulum değil).
- test_signature_defaults_match_the_registry_constants: imza <-> sabit pini.
"""
import inspect
from types import SimpleNamespace

from app import agent_registry, approvals, config, tools
from app.memory import Memory
from tests.fakes import FakeDB, wired_into_all_tools

USER = "kadir@example.com"
SESSION_ID = "s1"


def _ctx(user_id=USER, session_id=SESSION_ID):
    return SimpleNamespace(session=SimpleNamespace(user_id=user_id, id=session_id))


def _db():
    db = FakeDB()
    tools.init(Memory(db))
    return db


def _propose(*, name="ozel_arastirmaci", purpose="Nöbet raporlarını derler.",
             instruction="Sen nöbet raporu derleyen bir uzmansın.",
             tool_csv="check_my_vitals,list_reminders",
             why="Kadir her sabah aynı raporu istiyor.",
             evidence="Son hafta 5 kez nobetci şablonu aynı hedefle koştu.",
             ctx=None, max_steps=24, ttl_seconds=90):
    return tools.propose_agent(name, purpose, instruction, tool_csv, why, evidence,
                               ctx or _ctx(), max_steps=max_steps, ttl_seconds=ttl_seconds)


def _approvals(db):
    return list(db.collection(approvals.COLLECTION).docs.items())


def _registry(db):
    return db.collection(agent_registry.COLLECTION).docs


def test_propose_agent_writes_nothing_to_the_registry():
    """TAŞIYICI PİM (spec §5). Öneri bir onay kurar; ajan kayıt defteri BOŞ kalır."""
    db = _db()
    msg = _propose()
    assert _registry(db) == {}
    (aid, doc), = _approvals(db)
    assert doc["kind"] == approvals.KIND_AGENT_GRANT
    assert doc["status"] == approvals.STATUS_PENDING
    assert doc["tool_name"] == "ozel_arastirmaci"
    assert doc["tool_args"]["approval_id"] == aid
    assert "ozel_arastirmaci" in msg


def test_propose_agent_document_carries_operand_cause_and_reversibility():
    """propose_agent twin of test_tool_proposals'
    test_propose_tool_document_carries_operand_cause_and_reversibility:
    reversible must come from the new agent's OWN name ("ozel_arastirmaci"),
    never from "propose_agent" (also declared True in TOOL_REVERSIBILITY)."""
    db = _db()
    _propose(name="ozel_arastirmaci")
    (_aid, doc), = _approvals(db)
    assert doc["operand"] == "ozel_arastirmaci"
    assert doc["cause"] == approvals.CAUSE_CAPABILITY_REQUEST
    assert doc["reversible"] is False


def test_the_card_shows_the_full_instruction_and_evidence():
    """Kart onayı fiilen kod incelemesidir (spec §4): talimat ve kanıt detayda."""
    db = _db()
    _propose()
    (_, doc), = _approvals(db)
    assert "Sen nöbet raporu derleyen bir uzmansın." in doc["detail"]
    assert "Son hafta 5 kez" in doc["detail"]
    assert "check_my_vitals" in doc["detail"]


def test_propose_agent_rejects_a_red_tool_without_creating_a_card():
    db = _db()
    msg = _propose(tool_csv="check_my_vitals,cancel_reminder")
    assert "kırmızı" in msg.lower()
    assert _approvals(db) == [] and _registry(db) == {}


def test_propose_agent_rejects_a_shipped_template_name():
    db = _db()
    msg = _propose(name="arastirmaci")
    assert "sevkiyat" in msg.lower() or "gölge" in msg.lower()
    assert _approvals(db) == []


def test_propose_agent_rejects_an_unknown_tool():
    db = _db()
    msg = _propose(tool_csv="uydurma_arac")
    assert "uydurma_arac" in msg
    assert _approvals(db) == []


def test_propose_agent_rejects_the_spawn_tool():
    db = _db()
    msg = _propose(tool_csv="spawn_specialist")
    assert "recursion" in msg.lower() or "üretemez" in msg.lower()
    assert _approvals(db) == []


def test_propose_agent_rejects_cap_overflow():
    db = _db()
    assert "adım" in _propose(max_steps=33).lower()
    assert "süre" in _propose(ttl_seconds=121).lower()
    assert _approvals(db) == []


def test_propose_agent_refuses_an_already_granted_name():
    db = _db()
    _propose()
    (aid, _), = _approvals(db)
    approvals.decide(db, aid, USER, "approved", executors=approvals.EXECUTORS)
    msg = _propose()
    assert "zaten" in msg.lower()
    assert len(_approvals(db)) == 1          # ikinci kart YOK


def test_propose_agent_does_not_create_a_second_card_for_a_pending_name():
    db = _db()
    _propose()
    msg = _propose()
    assert len(_approvals(db)) == 1
    assert "bekleyen" in msg.lower() or "ikinci" in msg.lower()


def test_approved_agent_grant_writes_the_registry_document():
    """ÜRETİMİN GERÇEK DİZİSİ: request -> decide(approved) -> yürütücü -> grant."""
    db = _db()
    _propose()
    (aid, _), = _approvals(db)
    result = approvals.decide(db, aid, USER, "approved", executors=approvals.EXECUTORS)
    assert result["status"] == approvals.STATUS_APPROVED
    doc = _registry(db)["ozel_arastirmaci"]
    assert doc["status"] == agent_registry.STATUS_GRANTED
    assert doc["tools"] == ["check_my_vitals", "list_reminders"]
    assert doc["max_steps"] == 24 and doc["ttl_seconds"] == 90
    assert doc["approval_id"] == aid
    assert "yazıldı" in (result["outcome"] or "")


def test_rejected_agent_grant_writes_nothing():
    db = _db()
    _propose()
    (aid, _), = _approvals(db)
    approvals.decide(db, aid, USER, "rejected", reason="test",
                     executors=approvals.EXECUTORS)
    assert _registry(db) == {}


def test_executor_revalidates_corrupted_args():
    """Öneriyle karar arasında dünya değişebilir (spec §9.2): bozuk tool_args
    kayıt YAZMAZ, Türkçe gözlem döner."""
    msg = tools._execute_agent_grant(
        {"name": "x", "purpose": "p", "instruction": "t",
         "tools": "cancel_reminder", "max_steps": "24", "ttl_seconds": "90",
         "why": "w", "evidence": "e", "approval_id": "a"}, USER)
    assert "kırmızı" in msg.lower()


def test_executor_survives_non_numeric_caps():
    msg = tools._execute_agent_grant(
        {"name": "x", "purpose": "p", "instruction": "t",
         "tools": "check_my_vitals", "max_steps": "bozuk", "ttl_seconds": "90",
         "why": "w", "evidence": "e", "approval_id": "a"}, USER)
    assert "tavan" in msg.lower() or "adım" in msg.lower()


def test_propose_agent_is_green_and_registered_as_a_tool():
    assert config.TOOL_ZONES["propose_agent"] == config.ZONE_GREEN
    assert wired_into_all_tools(tools.propose_agent)


def test_signature_defaults_match_the_registry_constants():
    """`tools.propose_agent` imzasındaki `max_steps=24`/`ttl_seconds=90`
    literalleri ADK şeması yüzünden `agent_registry.DEFAULT_*` sabitlerine
    REFERANS VEREMEZ (Python varsayılanları literal int olmalı) -- bu pin
    testi ikisinin elle senkron tutulduğunu doğrular. `tools_mod.propose_agent`
    modül-seviyesi SENKRON orijinaldir; `ALL_TOOLS` bunun `_off_loop` sarmalı
    kopyasını taşır (bkz. tools.py ALL_TOOLS docstring'i)."""
    sig = inspect.signature(tools.propose_agent)
    assert sig.parameters["max_steps"].default == agent_registry.DEFAULT_MAX_STEPS
    assert sig.parameters["ttl_seconds"].default == agent_registry.DEFAULT_TTL_SECONDS


def test_backend_failure_is_an_observation():
    """İlke 4: araç fırlatmaz, gözlem döner (test_tool_proposals ikizi)."""

    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    tools.init(Memory(BoomDB()))
    out = _propose()
    assert isinstance(out, str) and ("oluşturulam" in out.lower() or "kapalı" in out.lower())
