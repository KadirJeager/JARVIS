"""Wear W0 — ortak kimlik kapısı (spec §4.3).

Taşıyıcı pimler:
- jdt_ öneki kayıt defterine, gerisi Google'a gider; iki yol birbirine sızmaz.
- Cihaz token'ı token YÖNETEMEZ (require_google_user 403).
- Ses WS handshake'i ortak kapının TA KENDİSİNİ kullanır (isim pini).
"""
import pytest

import app.auth as auth_mod
import app.voice as voice_mod
from app import device_tokens
from app.memory import Memory
from tests.fakes import FakeDB

EMAIL = "owner@example.com"


@pytest.fixture()
def db(monkeypatch):
    db = FakeDB()
    auth_mod.init(lambda: db)
    yield db
    auth_mod.init(None)


def _mint(db):
    return device_tokens.mint(db, email=EMAIL, device="watch-ultra")


def test_a_device_token_verifies_through_the_common_gate(db):
    out = _mint(db)
    assert auth_mod.verify_bearer_email(out["token"]) == EMAIL


def test_a_google_token_still_goes_to_google(db, monkeypatch):
    seen = {}

    def fake_google(token):
        seen["token"] = token
        return EMAIL

    monkeypatch.setattr(auth_mod, "verify_token_email", fake_google)
    assert auth_mod.verify_bearer_email("google-jwt") == EMAIL
    assert seen["token"] == "google-jwt"


def test_an_uninitialised_gate_fails_closed_for_device_tokens(monkeypatch):
    auth_mod.init(None)
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        auth_mod.verify_bearer_email("jdt_herhangi")


def test_require_user_accepts_a_device_token(db):
    out = _mint(db)
    assert auth_mod.require_user(f"Bearer {out['token']}") == EMAIL


def test_require_google_user_rejects_device_tokens_with_403(db):
    out = _mint(db)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        auth_mod.require_google_user(f"Bearer {out['token']}")
    assert exc.value.status_code == 403


def test_require_google_user_passes_google_tokens(db, monkeypatch):
    monkeypatch.setattr(auth_mod, "verify_token_email", lambda t: EMAIL)
    assert auth_mod.require_google_user("Bearer google-jwt") == EMAIL


def test_the_voice_handshake_uses_the_common_gate():
    """İsim pini: voice.py ortak kapıyı import eder — cihaz token'ı ses WS'ini
    bedavaya açar (spec §4.3). Bu pin düşerse saat sesli oturumdan sessizce
    dışlanmış demektir."""
    assert voice_mod.verify_bearer_email is auth_mod.verify_bearer_email


def test_guest_gate_stays_google_only():
    """BİLİNÇLİ sınır (plan Global Constraints): MCP misafir kapısı cihaz
    token'ı TANIMAZ."""
    import app.guest_gate as guest_mod

    assert getattr(guest_mod, "verify_token_email", None) is auth_mod.verify_token_email
    assert not hasattr(guest_mod, "verify_bearer_email")
