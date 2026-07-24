"""Dilim 3d spec §4.1: anchors and adaptive share ONE sample shape with stable
ids. Legacy shapes (bare vectors / {vec, device_hint, ts}) still LOAD -- dev-time
fixtures exist in them -- but everything SAVED is full samples. Production has no
data yet (spec §3), so no migration path beyond this normalization is needed."""
from app.speaker import SpeakerProfile, make_sample
from app.speaker_store import enroll_anchors, load_profile, save_profile
from tests.fakes import FakeDB

A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]


def test_legacy_shapes_normalize_to_full_samples():
    p = SpeakerProfile(anchors=[A],
                       adaptive=[{"vec": B, "device_hint": "phone", "ts": "t0"}])
    a = p.anchors[0]
    assert a["vec"] == A and a["source"] == "enroll" and a["id"]
    assert a["label"] is None and a["note"] is None and a["device_hint"] == "unknown"
    ad = p.adaptive[0]
    assert ad["vec"] == B and ad["source"] == "auto" and ad["id"]
    assert ad["device_hint"] == "phone" and ad["ts"] == "t0"


def test_sample_ids_are_distinct_and_survive_a_save_load_round_trip():
    db = FakeDB()
    p = SpeakerProfile(anchors=[A, B], adaptive=[])
    ids = [s["id"] for s in p.anchors]
    assert len(set(ids)) == 2
    save_profile(db, "k", p)
    assert [s["id"] for s in load_profile(db, "k").anchors] == ids


def test_scoring_is_unchanged_by_the_schema():
    """The schema is metadata-only: cosine math must see exactly the same
    vectors as before."""
    p = SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "p", "ts": "t"}])
    assert p.score(A, top_k=1) == 1.0
    assert p.anchor_score(B, top_k=1) == 0.0     # adaptive is invisible to ADAPT


def test_enroll_anchors_writes_full_samples_with_device_hint():
    db = FakeDB()
    ids = iter(["i1", "i2"])
    enroll_anchors(db, "k", [A, B], device_hint="phone",
                   now_fn=lambda: "t1", id_fn=lambda: next(ids))
    anchors = load_profile(db, "k").anchors
    assert [a["id"] for a in anchors] == ["i1", "i2"]
    assert all(a["source"] == "enroll" and a["device_hint"] == "phone"
               and a["ts"] == "t1" for a in anchors)


def test_adapt_returns_the_new_sample_id():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    sid = p.adapt(B, "phone", cap=5, now_fn=lambda: "t", id_fn=lambda: "new-id")
    assert sid == "new-id"
    assert p.adaptive[0]["id"] == "new-id" and p.adaptive[0]["source"] == "auto"


def test_eviction_never_removes_a_manual_sample():
    """spec §5: a manual sample is user-curated -- only an explicit DELETE or a
    reject correction removes it. cap=1 leaves one auto slot; the manual
    near-duplicate of the anchor would be THE most redundant sample under the
    old rule, so surviving here is discriminating."""
    manual = make_sample(A, "manual", "phone", "t0", "m1")
    p = SpeakerProfile(anchors=[A], adaptive=[manual])
    p.adapt(A, "phone", cap=1, now_fn=lambda: "t1", id_fn=lambda: "auto1")
    p.adapt(B, "phone", cap=1, now_fn=lambda: "t2", id_fn=lambda: "auto2")
    assert [s["id"] for s in p.adaptive if s["source"] == "manual"] == ["m1"]
    assert sum(1 for s in p.adaptive if s["source"] == "auto") == 1


def test_adaptive_cap_counts_only_auto_samples():
    """The auto cap (20 in prod) and the manual cap (5, Task 6) are separate
    budgets: a manual sample must not consume an auto slot."""
    manual = make_sample(B, "manual", "phone", "t0", "m1")
    p = SpeakerProfile(anchors=[A], adaptive=[manual])
    p.adapt(A, "phone", cap=1, now_fn=lambda: "t1", id_fn=lambda: "auto1")
    assert len(p.adaptive) == 2          # manual + 1 auto, nothing evicted
