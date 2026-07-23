"""Tests for tiered memory skeleton."""
from app.memory import FirestoreAudit, Memory
from tests.fakes import FakeDB


def test_profile_roundtrip():
    m = Memory(FakeDB())
    assert m.get_profile() == {}
    m.update_profile({"name": "Kadir", "coffee": "X kafeden"})
    assert m.get_profile()["coffee"] == "X kafeden"


def test_remember_fact_and_search():
    m = Memory(FakeDB())
    m.remember_fact("Kadir salı akşamları aranmak istemiyor")
    hits = m.search_memory("salı")
    assert len(hits) == 1 and "salı" in hits[0]["text"]


def test_add_lesson_shape():
    db = FakeDB()
    m = Memory(db)
    m.add_lesson("sipariş", "eski akış", "buton değişmiş", "yeni akış şu")
    lessons = list(db.collection("lessons").docs.values())
    assert lessons[0]["went_wrong"] == "buton değişmiş"


def test_session_snapshot_written():
    db = FakeDB()
    m = Memory(db)
    m.snapshot_session("s1", "kadir", {"turns": 3, "summary": "tanışma"})
    assert db.collection("sessions").docs["s1"]["summary"] == "tanışma"


def test_firestore_audit_writes_to_audit_log():
    db = FakeDB()
    FirestoreAudit(db).write({"tool": "x", "decision": "allow"})
    assert list(db.collection("audit_log").docs.values())[0]["decision"] == "allow"
