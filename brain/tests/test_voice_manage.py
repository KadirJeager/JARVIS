"""Dilim 3d spec §6: management endpoints. Fixture mirrors test_enroll.py's;
TestClient targets main.app so router MOUNTING is load-bearing (a forgotten
include_router turns every one of these into a 404)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app import speaker_history
from app.auth import require_user
from app.speaker import make_sample
from app.speaker_store import enroll_anchors, load_profile, save_profile
from tests.fakes import FakeDB

USER = "kadir@example.com"
A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]


@pytest.fixture
def manage_client(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: db, raising=False)
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    with TestClient(main_mod.app) as c:
        yield c, db
    main_mod.app.dependency_overrides.clear()


def _auth():
    main_mod.app.dependency_overrides[require_user] = lambda: USER


def _entry(i, *, score=0.8, verified=True, device="phone", adapted=None):
    return {"id": f"e{i}", "ts": f"t{i}", "score": score, "verified": verified,
            "device_hint": device, "presence": "locked", "trust_level": "MEDIUM",
            "adapted_sample_id": adapted, "correction": None, "vec": [0.1, 0.2]}


def _assert_no_vec(obj):
    """The spec §6 privacy claim, pinned recursively over the whole body."""
    if isinstance(obj, dict):
        assert "vec" not in obj
        for v in obj.values():
            _assert_no_vec(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_vec(v)


def test_profile_requires_auth(manage_client):
    c, _db = manage_client
    assert c.get("/api/voice/profile").status_code in (401, 403)


def test_empty_profile_has_the_full_shape(manage_client):
    c, _db = manage_client
    _auth()
    r = c.get("/api/voice/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"anchors": 0, "auto": 0, "manual": 0}
    assert body["samples"] == [] and body["history"] == []
    assert body["quality"]["mean_verified_score"] is None


def test_profile_reports_counts_samples_history_without_vectors(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], device_hint="phone",
                   now_fn=lambda: "t0", id_fn=lambda: "a1")
    profile = load_profile(db, USER)
    profile.adaptive.append(make_sample(B, "auto", "headset", "t1", "s1"))
    profile.adaptive.append(make_sample(B, "manual", "phone", "t2", "s2", label="hasta"))
    save_profile(db, USER, profile)
    speaker_history.record(db, USER, _entry(1, adapted="s1"), cap=50)

    body = c.get("/api/voice/profile").json()
    assert body["counts"] == {"anchors": 1, "auto": 1, "manual": 1}
    assert [s["id"] for s in body["samples"]] == ["a1", "s1", "s2"]
    assert body["samples"][2]["label"] == "hasta"
    assert [e["id"] for e in body["history"]] == ["e1"]
    assert body["history"][0]["trust_level"] == "MEDIUM"
    _assert_no_vec(body)


def test_profile_read_failure_is_a_turkish_502(manage_client, monkeypatch):
    c, _db = manage_client
    _auth()
    import app.speaker_store as store_mod

    def boom(db_, user_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(store_mod, "load_profile", boom)
    r = c.get("/api/voice/profile")
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]


# --- quality_indicators: pure, unit-tested without HTTP ----------------------


def test_quality_mean_fail_rate_and_device_breakdown():
    from app.voice_manage import quality_indicators
    entries = [_entry(1, score=0.6), _entry(2, score=0.8),
               _entry(3, score=0.2, verified=False, device="headset")]
    q = quality_indicators(entries, {})
    assert q["mean_verified_score"] == pytest.approx(0.7)
    assert q["fail_rate"] == pytest.approx(1 / 3)
    assert q["by_device"]["phone"] == pytest.approx(0.7)
    assert q["by_device"]["headset"] == pytest.approx(0.2)


def test_quality_label_breakdown_follows_the_adapted_sample(manage_client):
    from app.voice_manage import quality_indicators
    samples = {"s1": make_sample(A, "auto", "phone", "t", "s1", label="gurultulu")}
    entries = [_entry(1, score=0.4, adapted="s1"), _entry(2, score=0.9)]
    q = quality_indicators(entries, samples)
    assert q["by_label"] == {"gurultulu": pytest.approx(0.4)}


def test_quality_trend_compares_last_10_with_previous_10():
    from app.voice_manage import quality_indicators
    entries = [_entry(i, score=0.2) for i in range(10)] + \
              [_entry(10 + i, score=0.8) for i in range(10)]
    q = quality_indicators(entries, {})
    assert q["trend"] == {"last10": pytest.approx(0.8),
                          "previous10": pytest.approx(0.2)}


def test_quality_of_empty_history_is_all_none():
    from app.voice_manage import quality_indicators
    q = quality_indicators([], {})
    assert q["mean_verified_score"] is None and q["fail_rate"] is None
    assert q["by_device"] == {} and q["by_label"] == {}
    assert q["trend"] == {"last10": None, "previous10": None}


# --- PATCH/DELETE /api/voice/sample/{id} (spec §6, §8) ----------------------


def _seed_profile(db):
    """2 anchors + 1 auto sample with known ids."""
    ids = iter(["a1", "a2"])
    enroll_anchors(db, USER, [A, B], device_hint="phone",
                   now_fn=lambda: "t0", id_fn=lambda: next(ids))
    profile = load_profile(db, USER)
    profile.adaptive.append(make_sample(B, "auto", "headset", "t1", "s1"))
    save_profile(db, USER, profile)


def test_patch_sample_updates_label_and_note(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.patch("/api/voice/sample/s1", json={"label": "gurultulu", "note": "metroda"})
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "gurultulu" and body["note"] == "metroda"
    _assert_no_vec(body)
    stored = load_profile(db, USER).adaptive[0]
    assert stored["label"] == "gurultulu" and stored["note"] == "metroda"


def test_patch_note_only_preserves_the_label(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    c.patch("/api/voice/sample/s1", json={"label": "hasta"})
    r = c.patch("/api/voice/sample/s1", json={"note": "sadece not"})
    assert r.status_code == 200
    assert r.json()["label"] == "hasta"          # omitted field untouched


def test_patch_label_null_clears_it(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    c.patch("/api/voice/sample/s1", json={"label": "hasta"})
    r = c.patch("/api/voice/sample/s1", json={"label": None})
    assert r.status_code == 200 and r.json()["label"] is None


def test_patch_rejects_a_label_outside_the_fixed_set(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.patch("/api/voice/sample/s1", json={"label": "nezleli"})
    assert r.status_code == 400
    assert "Geçersiz etiket" in r.json()["detail"]
    assert load_profile(db, USER).adaptive[0]["label"] is None


def test_patch_unknown_sample_is_404(manage_client):
    c, _db = manage_client
    _auth()
    r = c.patch("/api/voice/sample/yok", json={"label": "hasta"})
    assert r.status_code == 404


def test_delete_adaptive_sample(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.delete("/api/voice/sample/s1")
    assert r.status_code == 200 and r.json() == {"deleted": "s1"}
    assert load_profile(db, USER).adaptive == []


def test_delete_an_anchor_when_others_remain(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.delete("/api/voice/sample/a1")
    assert r.status_code == 200
    assert [s["id"] for s in load_profile(db, USER).anchors] == ["a2"]


def test_the_last_anchor_cannot_be_deleted(manage_client):
    """spec §8: an anchorless profile cannot score ACCEPT and leaves ADAPT
    refereeing without a reference -- an explicit 400 beats a silently
    non-functional profile."""
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], device_hint="phone",
                   now_fn=lambda: "t0", id_fn=lambda: "a1")
    r = c.delete("/api/voice/sample/a1")
    assert r.status_code == 400
    assert "Son çapa" in r.json()["detail"]
    assert len(load_profile(db, USER).anchors) == 1


def test_delete_unknown_sample_is_404(manage_client):
    c, _db = manage_client
    _auth()
    assert c.delete("/api/voice/sample/yok").status_code == 404


def test_sample_endpoints_require_auth(manage_client):
    c, _db = manage_client
    assert c.patch("/api/voice/sample/s1", json={"label": "hasta"}).status_code in (401, 403)
    assert c.delete("/api/voice/sample/s1").status_code in (401, 403)
