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
