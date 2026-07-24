from app.speaker import SpeakerProfile
from app.speaker_store import load_profile, save_profile, enroll_anchors
from tests.fakes import FakeDB

A = [1.0, 0.0]
B = [0.0, 1.0]

def test_load_missing_returns_empty_profile():
    p = load_profile(FakeDB(), "kadir@example.com")
    assert p.anchors == [] and p.adaptive == []

def test_save_then_load_round_trips():
    db = FakeDB()
    save_profile(db, "kadir@example.com",
                 SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "phone", "ts": "t"}]))
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A]
    assert p.adaptive == [{"vec": B, "device_hint": "phone", "ts": "t"}]

def test_enroll_anchors_appends():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A, B])
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A, B]

def test_enroll_anchors_appends_to_pre_existing_anchors():
    """Starts from a NON-empty gallery -- an overwrite bug (anchors = vecs)
    would pass test_enroll_anchors_appends above (empty start) but would lose
    the pre-existing anchor here. Both old and new must survive, in order."""
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A])
    enroll_anchors(db, "kadir@example.com", [B])
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A, B]

def test_enroll_anchors_does_not_wipe_existing_adaptive():
    db = FakeDB()
    save_profile(db, "kadir@example.com",
                 SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "phone", "ts": "t"}]))
    enroll_anchors(db, "kadir@example.com", [B])
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A, B]
    assert p.adaptive == [{"vec": B, "device_hint": "phone", "ts": "t"}]

def test_enroll_anchors_empty_list_is_safe_noop():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A])
    enroll_anchors(db, "kadir@example.com", [])
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A]

def test_profiles_are_user_keyed():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A])
    assert load_profile(db, "someone@else.com").anchors == []
