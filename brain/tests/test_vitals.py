"""check_my_vitals aracı + vitals sayaçları (North Star §4.5, Faz Y1).

Gerçek Firestore/GCP yok: FakeDB üzerinde; main/voice/policy entegrasyonları
mevcut test desenleriyle (monkeypatch + fake runner/ws) sınanır.
"""
from types import SimpleNamespace

import pytest

import app.main as main_mod
from app import config, messages, tools, vitals
from app.memory import Memory
from app.policy import make_policy_callback
from app.voice import VoiceBridge
from tests.fakes import FakeDB, FakeRunner, wired_into_all_tools


def _counters(db):
    return vitals.read_counters(db)


# --- vitals.bump: sayaç artışı ve gün sıfırlaması -----------------------------


def test_bump_increments_and_stamps_last_reset():
    db = FakeDB()
    vitals.bump(db, "chat_turns_today")
    vitals.bump(db, "chat_turns_today")
    vitals.bump(db, "voice_turns_today")
    counters = _counters(db)
    assert counters["chat_turns_today"] == 2
    assert counters["voice_turns_today"] == 1
    assert counters["last_reset"] == vitals._today()


def test_bump_resets_counters_when_the_utc_day_changes():
    """Dün doldurulmuş sayaçlar, bugünkü ilk yazımda sıfırlanıp 1'den başlar."""
    db = FakeDB()
    vitals.bump(db, "chat_turns_today", today="2026-07-30")
    vitals.bump(db, "chat_turns_today", today="2026-07-30")
    vitals.bump(db, "tool_calls_today", today="2026-07-30")
    vitals.bump(db, "chat_turns_today", today="2026-07-31")
    counters = _counters(db)
    assert counters["chat_turns_today"] == 1
    assert counters["tool_calls_today"] == 0  # gün sıfırlaması tüm sayaçları kapsar
    assert counters["last_reset"] == "2026-07-31"


def test_bump_rejects_unknown_counter():
    with pytest.raises(ValueError):
        vitals.bump(FakeDB(), "kafein_seviyesi")


# --- vitals.read: boş counters, audit listesi, uydurmasız servis alanı --------


def test_read_returns_empty_quota_without_counters_doc():
    """Counters dokümanı hiç yazılmamışsa kota {} döner — hata değil."""
    result = vitals.read(FakeDB())
    assert result["kota"] == {}
    assert result["son_hatalar"] == []
    assert result["aylik_harcama"] is None


def test_read_full_shape():
    db = FakeDB()
    vitals.bump(db, "chat_turns_today")
    result = vitals.read(db)
    assert set(result) == {"servisler", "kota", "son_hatalar", "aylik_harcama"}
    assert result["kota"]["chat_turns_today"] == 1


def test_service_facts_hold_only_real_process_values():
    """Uydurma metrik YOK: alan kümesi tam olarak process içi gerçeklerdir
    (min_instance, cpu, bellek gibi okunamaz değerler şemada bile yoktur)."""
    facts = vitals.service_facts()
    assert set(facts) == {"baslangic_utc", "uptime_saniye", "revizyon"}
    assert isinstance(facts["uptime_saniye"], int) and facts["uptime_saniye"] >= 0
    assert isinstance(facts["baslangic_utc"], str)
    assert facts["revizyon"] is None or isinstance(facts["revizyon"], str)


def test_recent_blocks_lists_block_and_confirm_only_newest_first():
    db = FakeDB()
    audit = db.collection("audit_log")
    audit.add({"ts": "2026-07-31T10:00:00+00:00", "actor": "orchestrator",
               "tool": "t1", "zone": "red", "decision": "block"})
    audit.add({"ts": "2026-07-31T11:00:00+00:00", "actor": "orchestrator",
               "tool": "t2", "zone": "green", "decision": "allow"})  # sayılmaz
    audit.add({"ts": "2026-07-31T12:00:00+00:00", "actor": "guest:a@b.c",
               "tool": "t3", "zone": "yellow", "decision": "confirm"})
    blocks = vitals.recent_blocks(db)
    assert [b["tool"] for b in blocks] == ["t3", "t1"]  # allow elenir, ts desc
    assert all(b["decision"] in ("block", "confirm") for b in blocks)


def test_recent_blocks_caps_at_five():
    db = FakeDB()
    for i in range(7):
        db.collection("audit_log").add({
            "ts": f"2026-07-31T1{i}:00:00+00:00", "actor": "orchestrator",
            "tool": f"t{i}", "zone": "red", "decision": "block",
        })
    blocks = vitals.recent_blocks(db)
    assert len(blocks) == 5
    assert blocks[0]["tool"] == "t6"  # en yeni 5


# --- check_my_vitals aracı ----------------------------------------------------


def test_check_my_vitals_tool_returns_vitals_dict():
    db = FakeDB()
    vitals.bump(db, "chat_turns_today")
    tools.init(Memory(db))
    result = tools.check_my_vitals()
    assert "hata" not in result
    assert result["kota"]["chat_turns_today"] == 1
    assert result["aylik_harcama"] is None
    assert set(result["servisler"]) == {"baslangic_utc", "uptime_saniye", "revizyon"}


def test_check_my_vitals_is_registered_green():
    assert wired_into_all_tools(tools.check_my_vitals)
    assert config.TOOL_ZONES["check_my_vitals"] == config.ZONE_GREEN


# --- Sayaç entegrasyonu: policy allow ------------------------------------------


class _DBAudit:
    """FirestoreAudit yüzeyi: write() + .db (sayacın eriştiği tek kanal)."""

    def __init__(self, db):
        self.db = db
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def _tool(name):
    return SimpleNamespace(name=name)


def test_policy_allow_counts_the_tool_call():
    db = FakeDB()
    cb = make_policy_callback(_DBAudit(db))
    assert cb(_tool("get_user_profile"), {}, None) is None
    assert _counters(db)["tool_calls_today"] == 1


def test_policy_block_and_dry_run_do_not_count(monkeypatch):
    db = FakeDB()
    audit = _DBAudit(db)
    cb = make_policy_callback(audit)
    cb(_tool("unknown_danger"), {}, None)            # block
    monkeypatch.setattr(config, "DRY_RUN", True)
    cb(_tool("get_user_profile"), {}, None)          # dry_run
    assert _counters(db) == {}  # hiç sayaç dokümanı bile oluşmamalı
    assert [e["decision"] for e in audit.entries] == ["block", "dry_run"]


def test_policy_audit_without_db_is_skipped():
    """db taşımayan audit yazıcısı (test fake'i profili) sayacı sessizce atlar."""

    class BareAudit:
        def __init__(self):
            self.entries = []

        def write(self, entry):
            self.entries.append(entry)

    cb = make_policy_callback(BareAudit())
    assert cb(_tool("get_user_profile"), {}, None) is None  # hata yok


# --- Sayaç entegrasyonu: main.run_turn -----------------------------------------


@pytest.mark.asyncio
async def test_run_turn_counts_a_successful_chat_turn(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", messages.MessageStore(db))
    monkeypatch.setattr(main_mod, "_runner", FakeRunner())
    monkeypatch.setattr(main_mod, "_conversations", None)

    reply = await main_mod.run_turn("owner@example.com", "s1", "selam")

    assert reply == "cevap"
    assert _counters(db)["chat_turns_today"] == 1


@pytest.mark.asyncio
async def test_run_turn_survives_a_counter_failure(monkeypatch, caplog):
    """Best effort sözleşmesi: sayaç yazımı patlarsa cevap yine de döner."""
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", messages.MessageStore(db))
    monkeypatch.setattr(main_mod, "_runner", FakeRunner())
    monkeypatch.setattr(main_mod, "_conversations", None)

    def boom(db, field, **kw):
        raise RuntimeError("firestore yok")

    monkeypatch.setattr(main_mod.vitals, "bump", boom)
    with caplog.at_level("ERROR"):
        reply = await main_mod.run_turn("owner@example.com", "s1", "selam")
    assert reply == "cevap"
    assert "vitals sayacı yazılamadı" in caplog.text


# --- Sayaç entegrasyonu: voice._serve_turn --------------------------------------


class _FakeWS:
    """_serve_turn'ün kullandığı tek yüzey: send_text."""

    def __init__(self):
        self.sent = []

    async def send_text(self, t):
        self.sent.append(t)


@pytest.mark.asyncio
async def test_voice_turn_counts_a_successful_voice_turn():
    db = FakeDB()
    bridge = VoiceBridge(
        runner=FakeRunner(), session_service=None, memory=SimpleNamespace(db=db)
    )
    bridge._user_id = "u"
    bridge._session_id = "s"
    ws = _FakeWS()
    await bridge._serve_turn(ws, "selam")
    assert _counters(db)["voice_turns_today"] == 1


@pytest.mark.asyncio
async def test_voice_turn_failure_does_not_count():
    """Runner patlarsa tur sayılmaz (evt_error yolu); sayaç dokümanı oluşmaz."""
    class BoomRunner:
        def run_async(self, **kw):
            raise RuntimeError("runner patladı")

    db = FakeDB()
    bridge = VoiceBridge(
        runner=BoomRunner(), session_service=None, memory=SimpleNamespace(db=db)
    )
    bridge._user_id = "u"
    bridge._session_id = "s"
    await bridge._serve_turn(_FakeWS(), "selam")
    assert _counters(db) == {}


@pytest.mark.asyncio
async def test_voice_turn_without_memory_skips_the_counter():
    """memory=None test bridge'i: sayaç atlanır, tur normal kapanır."""
    bridge = VoiceBridge(runner=FakeRunner(), session_service=None, memory=None)
    bridge._user_id = "u"
    bridge._session_id = "s"
    await bridge._serve_turn(_FakeWS(), "selam")  # hata yok
