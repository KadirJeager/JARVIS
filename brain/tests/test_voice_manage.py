"""Dilim 3d spec §6: management endpoints. Fixture mirrors test_enroll.py's;
TestClient targets main.app so router MOUNTING is load-bearing (a forgotten
include_router turns every one of these into a 404)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app import speaker, speaker_history
from app.auth import require_google_user, require_user
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
    main_mod.app.dependency_overrides[require_google_user] = lambda: USER


def _entry(i, *, score=0.8, verified=True, device="phone", adapted=None,
           cm_fake_prob=0.01):
    # cm_fake_prob default (0.01 = comfortably bonafide) keeps every
    # pre-existing caller of this helper clear of the CM confirm-gate; tests
    # that exercise the gate itself pass an explicit value.
    return {"id": f"e{i}", "ts": f"t{i}", "score": score, "verified": verified,
            "device_hint": device, "presence": "locked", "trust_level": "MEDIUM",
            "adapted_sample_id": adapted, "correction": None,
            "cm_fake_prob": cm_fake_prob, "vec": [0.1, 0.2]}


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


# --- POST /api/voice/history/{id}/confirm + /reject (spec §6) ---------------


def _seed_history(db, *, adapted=None, correction=None):
    speaker_history.record(db, USER, {
        "id": "e1", "ts": "t1", "score": 0.5, "verified": True,
        "device_hint": "phone", "presence": "locked", "trust_level": "MEDIUM",
        "adapted_sample_id": adapted, "correction": correction,
        "cm_fake_prob": 0.01, "vec": B,
    }, cap=50)


def _service_with_history(cm_fake_prob):
    """A SpeakerService whose history holds exactly one auto entry carrying the
    given CM score."""
    db = FakeDB()
    svc = speaker.SpeakerService(db)
    speaker_history.record(db, "kadir@example.com", {
        "id": "entry-1", "ts": "2026-08-11T00:00:00Z", "score": 0.72,
        "verified": True, "device_hint": "android-Pixel 10 Pro",
        "presence": "foreground", "trust_level": "HIGH",
        "adapted_sample_id": None, "correction": None,
        "cm_fake_prob": cm_fake_prob, "vec": [0.1] * 192,
    }, 50)
    return svc, db


def test_confirm_rejects_entry_the_cm_called_spoof():
    """The utterance was flagged at the time it was spoken; confirming it must
    not launder it into the gallery."""
    svc, db = _service_with_history(cm_fake_prob=0.95)
    with pytest.raises(speaker.RuleViolation):
        svc.confirm_history("kadir@example.com", "entry-1")


def test_confirm_rejects_entry_with_no_cm_evidence():
    """cm_fake_prob=None means the CM never answered for this utterance (timeout,
    disabled, or an old record). Absence of evidence is not evidence of absence."""
    svc, db = _service_with_history(cm_fake_prob=None)
    with pytest.raises(speaker.RuleViolation):
        svc.confirm_history("kadir@example.com", "entry-1")


def test_confirm_accepts_bonafide_entry():
    svc, db = _service_with_history(cm_fake_prob=0.01)
    out = svc.confirm_history("kadir@example.com", "entry-1")
    assert out["already"] is False
    assert out["added_sample_id"]


def test_confirm_endpoint_adds_a_manual_sample(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    _seed_history(db)
    r = c.post("/api/voice/history/e1/confirm")
    assert r.status_code == 200
    body = r.json()
    assert body["already"] is False and body["added_sample_id"]
    _assert_no_vec(body)
    manuals = [s for s in load_profile(db, USER).adaptive if s["source"] == "manual"]
    assert len(manuals) == 1


def test_reject_endpoint_marks_the_entry(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    _seed_history(db)
    r = c.post("/api/voice/history/e1/reject")
    assert r.status_code == 200 and r.json()["already"] is False
    assert speaker_history.load_history(db, USER)[0]["correction"] == "rejected"


def test_correction_of_a_missing_entry_is_404(manage_client):
    c, _db = manage_client
    _auth()
    r = c.post("/api/voice/history/yok/confirm")
    assert r.status_code == 404
    assert "artık yok" in r.json()["detail"]


def test_manual_cap_surfaces_as_a_400_with_the_count(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    profile = load_profile(db, USER)
    for i in range(5):
        profile.adaptive.append(make_sample(B, "manual", "phone", "t", f"m{i}"))
    save_profile(db, USER, profile)
    _seed_history(db)
    r = c.post("/api/voice/history/e1/confirm")
    assert r.status_code == 400 and "5/5" in r.json()["detail"]


def test_correction_endpoints_require_auth(manage_client):
    c, _db = manage_client
    assert c.post("/api/voice/history/e1/confirm").status_code in (401, 403)
    assert c.post("/api/voice/history/e1/reject").status_code in (401, 403)


def test_production_service_carries_the_config_manual_cap_and_labels(manage_client, monkeypatch):
    """Same wiring-guard class as Task 3's history_cap test. manual_cap's
    ctor default (5) coincidentally equals config's default (SPEAKER_MANUAL_CAP
    == 5), so asserting against the out-of-the-box default would not catch
    the wiring being dropped -- monkeypatch to a distinctive value, same fix
    as test_production_service_carries_the_config_history_cap in
    tests/test_speaker_service.py."""
    from app import config
    monkeypatch.setattr(config, "SPEAKER_MANUAL_CAP", 3)
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    svc = main_mod.get_speaker_service()
    assert svc.manual_cap == 3
    assert svc.labels == config.SPEAKER_SAMPLE_LABELS


# --- DELETE /api/voice/profile (spec §8: no half-deletion) ------------------


def test_profile_deletion_removes_profile_AND_history(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    _seed_history(db)
    r = c.delete("/api/voice/profile")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert db.collection("speaker_profiles").document(USER).get().exists is False
    assert db.collection("speaker_history").document(USER).get().exists is False


def test_profile_deletion_leaves_other_users_alone(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A])
    enroll_anchors(db, "baskasi@example.com", [B])
    c.delete("/api/voice/profile")
    assert len(load_profile(db, "baskasi@example.com").anchors) == 1


def test_profile_deletion_requires_auth(manage_client):
    c, _db = manage_client
    assert c.delete("/api/voice/profile").status_code in (401, 403)


def test_profile_deletion_failure_is_a_turkish_502(manage_client, monkeypatch):
    c, db = manage_client
    _auth()
    import app.speaker_store as store_mod

    def boom(db_, user_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(store_mod, "delete_profile", boom)
    r = c.delete("/api/voice/profile")
    assert r.status_code == 502 and "altyapı" in r.json()["detail"]


# --- spec §11: correction vs live identify, no lost writes ------------------


def test_concurrent_confirm_and_identify_do_not_lose_a_write():
    """The race the shared lock exists for, at the two mutation entry points
    Dilim 3d adds: a live identify() that adapts and a confirm_history() from
    the management surface interleave from two threads -- BOTH new samples
    must survive (same barrier pattern as test_enroll.py's enroll/identify
    race)."""
    import threading

    from app.speaker import SpeakerService

    db = FakeDB()
    ids = iter(f"id{i}" for i in range(10))
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t",
                         accept=0.35, adapt=0.6, cap=20, top_k=3,
                         id_fn=lambda: next(ids), manual_cap=5, history_cap=50)
    svc.enroll(USER, [A])
    _seed_history(db)

    start = threading.Barrier(2)
    errors = []

    def do_confirm():
        try:
            start.wait(timeout=5)
            svc.confirm_history(USER, "e1")
        except Exception as exc:            # pragma: no cover - reported below
            errors.append(exc)

    def do_identify():
        try:
            start.wait(timeout=5)
            svc.identify(USER, b"\x00\x01", "phone", auth_is_kadir=True, cm_ok=True)
        except Exception as exc:            # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=do_confirm), threading.Thread(target=do_identify)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, errors
    profile = load_profile(db, USER)
    assert sum(1 for s in profile.adaptive if s["source"] == "manual") == 1, \
        "the confirmed manual sample was lost to the adapt write"
    assert sum(1 for s in profile.adaptive if s["source"] == "auto") == 1, \
        "the adaptive sample was lost to the confirm write"
