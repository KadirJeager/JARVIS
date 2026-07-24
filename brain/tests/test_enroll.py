"""POST /api/voice/enroll: require_user-gated bootstrap enrollment of Kadir's
voiceprint anchors (Katman 2b Dilim 3a, spec §5). Torch-free: app.speaker.embed
is monkeypatched to a fixed vector, so this never touches the real ECAPA model."""
import base64

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.auth import require_user
from app.speaker_store import load_profile
from tests.fakes import FakeDB


@pytest.fixture
def enroll_client(monkeypatch):
    """A TestClient wired to a fresh FakeDB via _enroll_db, with embed() patched
    to a fixed vector (no torch). require_user is left un-overridden here so
    individual tests can opt in (auth-required tests rely on that)."""
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: db, raising=False)
    monkeypatch.setattr("app.speaker.embed", lambda pcm: [1.0, 0.0])
    with TestClient(main_mod.app) as c:
        yield c, db
    main_mod.app.dependency_overrides.clear()


def _clip(raw: bytes = b"\x00\x01\x00\x01") -> str:
    return base64.b64encode(raw).decode()


def test_enroll_stores_anchors(enroll_client):
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"
    r = c.post("/api/voice/enroll", json={"clips": [_clip(), _clip()]})
    assert r.status_code == 200
    assert r.json() == {"anchors": 2}
    assert len(load_profile(db, "kadir@example.com").anchors) == 2


def test_enroll_accumulates_across_calls(enroll_client):
    """A second enrollment call for the same user must ADD to, not replace,
    the existing anchors -- proves enroll_anchors (append) is wired, not
    save_profile (overwrite) called directly."""
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"
    c.post("/api/voice/enroll", json={"clips": [_clip()]})
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 200
    assert r.json() == {"anchors": 2}
    assert len(load_profile(db, "kadir@example.com").anchors) == 2


def test_enroll_requires_auth(enroll_client):
    """No dependency override -> require_user runs for real and rejects the
    unauthenticated request (matches test_api.py's *_requires_auth pattern)."""
    c, _db = enroll_client
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code in (401, 403)


def test_enroll_rejects_empty_clips(enroll_client):
    c, _db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"
    r = c.post("/api/voice/enroll", json={"clips": []})
    assert r.status_code == 400


def test_enroll_wraps_failure_as_502(enroll_client, monkeypatch):
    """A speaker.embed blowup (bad clip, model hiccup) must come back as the
    same graceful Turkish 502 pattern as /api/chat and /api/history, not a
    raw 500 -- and must not partially store anchors."""
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"

    def boom(pcm):
        raise RuntimeError("embed blew up")

    monkeypatch.setattr("app.speaker.embed", boom)
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 502
    assert r.json()["detail"] == "Ses kaydı işlenemedi, tekrar dene"
    assert load_profile(db, "kadir@example.com").anchors == []


def test_enroll_final_read_failure_is_502_not_500(enroll_client, monkeypatch):
    """The endpoint's final speaker_store.load_profile() call (to compute the
    returned anchor count) used to sit OUTSIDE the try/except that converts
    failures into the Turkish 502 -- so a transient Firestore error on THAT
    specific read (embed + enroll_anchors both already succeeded) escaped as
    a raw, unhandled 500 instead of the same 502 pattern every other failure
    in this endpoint gets. Only the SECOND load_profile call (the endpoint's
    own final read) is made to fail; the FIRST call (enroll_anchors' internal
    load-then-extend, called via the same module-level name) must still
    succeed, isolating the exact line under test."""
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"

    real_load_profile = main_mod.speaker_store.load_profile
    calls = {"n": 0}

    def flaky_load_profile(db_, user_id):
        calls["n"] += 1
        if calls["n"] > 1:            # 1st call = inside enroll_anchors (must succeed)
            raise RuntimeError("firestore hiccup on final read")
        return real_load_profile(db_, user_id)

    monkeypatch.setattr(main_mod.speaker_store, "load_profile", flaky_load_profile)
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 502
    assert r.json()["detail"] == "Ses kaydı işlenemedi, tekrar dene"
