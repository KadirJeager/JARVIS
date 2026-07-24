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

def test_profiles_are_user_keyed():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A])
    assert load_profile(db, "someone@else.com").anchors == []
