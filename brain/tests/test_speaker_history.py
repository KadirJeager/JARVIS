"""Dilim 3d spec §4.2: speaker_history/{user_id} holds a ring buffer of
verification outcomes. Embeddings are stored, audio never is."""
from app import speaker_history
from tests.fakes import FakeDB


def _entry(i):
    return {"id": f"e{i}", "ts": f"t{i}", "score": 0.5, "verified": True,
            "device_hint": "phone", "presence": "foreground",
            "trust_level": "HIGH", "adapted_sample_id": None,
            "correction": None, "vec": [1.0, 0.0]}


def test_empty_history_loads_as_empty_list():
    assert speaker_history.load_history(FakeDB(), "k") == []


def test_record_appends_in_order():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=50)
    speaker_history.record(db, "k", _entry(2), cap=50)
    assert [e["id"] for e in speaker_history.load_history(db, "k")] == ["e1", "e2"]


def test_ring_buffer_drops_the_oldest_beyond_cap():
    db = FakeDB()
    for i in range(51):
        speaker_history.record(db, "k", _entry(i), cap=50)
    entries = speaker_history.load_history(db, "k")
    assert len(entries) == 50
    assert entries[0]["id"] == "e1" and entries[-1]["id"] == "e50"


def test_history_is_user_keyed():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=50)
    assert speaker_history.load_history(db, "someone@else.com") == []


def test_delete_history_removes_the_document():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=50)
    speaker_history.delete_history(db, "k")
    assert speaker_history.load_history(db, "k") == []
    snap = db.collection("speaker_history").document("k").get()
    assert snap.exists is False, "doc must be GONE, not emptied -- deletion is deletion"


def test_cap_zero_keeps_no_history():
    """cap=0 must keep no entries, not unbounded history.
    The negative-slice trap: list[-0:] == list[:] (whole list).
    Same guard as SPEAKER_BARGE_IN_ONSET_BYTES in config.py."""
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=0)
    speaker_history.record(db, "k", _entry(2), cap=0)
    assert speaker_history.load_history(db, "k") == []


def test_cap_one_keeps_only_newest():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=1)
    speaker_history.record(db, "k", _entry(2), cap=1)
    entries = speaker_history.load_history(db, "k")
    assert len(entries) == 1
    assert entries[0]["id"] == "e2"
