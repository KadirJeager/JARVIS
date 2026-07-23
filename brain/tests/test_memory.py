"""Tests for tiered memory skeleton."""
from app.memory import FirestoreAudit, Memory, _merge_ranked
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


def test_search_uses_embed_fn_when_available():
    calls = []

    def fake_embed(text):
        calls.append(text)
        return [1.0, 0.0] if "kahve" in text else [0.0, 1.0]

    db = FakeDB()
    m = Memory(db, embed_fn=fake_embed)
    m.remember_fact("kahveyi X'ten söyler")
    m.remember_fact("salı akşamı arama")
    hits = m.search_memory("kahve nereden")
    assert calls  # embedding gerçekten çağrıldı
    assert "kahve" in hits[0]["text"]


def test_merge_ranked_orders_by_distance_ascending_across_sources():
    # Regression for the cross-collection ranking bug: a "facts" hit with a
    # larger (worse) distance must not out-rank a closer "lessons" hit just
    # because it happened to be appended first.
    hits = [
        (0.9, {"source": "facts", "text": "uzak fact"}),
        (0.1, {"source": "lessons", "text": "yakın lesson"}),
        (0.5, {"source": "facts", "text": "orta fact"}),
    ]
    result = _merge_ranked(hits, top_k=2)
    assert [h["text"] for h in result] == ["yakın lesson", "orta fact"]


def test_merge_ranked_respects_top_k_truncation():
    hits = [(float(i), {"source": "facts", "text": str(i)}) for i in range(5)]
    result = _merge_ranked(hits, top_k=3)
    assert [h["text"] for h in result] == ["0", "1", "2"]


def test_merge_ranked_empty_input():
    assert _merge_ranked([], top_k=5) == []
