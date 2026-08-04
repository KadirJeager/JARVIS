"""Wear W0 — ortak kimlik kapısı (spec §4.3).

Taşıyıcı pimler:
- jdt_ öneki kayıt defterine, gerisi Google'a gider; iki yol birbirine sızmaz.
- Cihaz token'ı token YÖNETEMEZ (require_google_user 403).
- Ses WS handshake'i ortak kapının TA KENDİSİNİ kullanır (isim pini).
"""
import pytest

import app.auth as auth_mod
import app.voice as voice_mod
from app import config, device_tokens
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


def test_a_gate_with_a_failing_db_provider_stays_closed_for_device_tokens():
    """C1 fix sonrası main.py kapıyı import ANINDA kendiliğinden-başlatan bir
    provider ile kayıtlar (main._device_token_db) -- None provider senaryosu
    üretimde artık oluşmaz. Kalan gerçek risk: provider kayıtlı ama db'ye
    erişim (soğuk başlangıçta _init dahil) patlıyor -- kapı yine fail-closed
    kalmalı."""
    auth_mod.init(lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    try:
        with pytest.raises(PermissionError, match="Geçersiz oturum"):
            auth_mod.verify_bearer_email("jdt_herhangi")
    finally:
        auth_mod.init(None)


def test_device_token_path_rechecks_the_allowlist(db, monkeypatch):
    """Important 3: Google yolu her istekte config.ALLOWED_EMAILS'i kontrol
    eder (verify_token_email); cihaz yolu basımdan sonra bunu bir daha hiç
    kontrol etmiyordu. Aynı ret metnini kullanır ki davranış sınıfı (403)
    eşleşsin -- ("Geçersiz oturum" 401'e, "Bu hesap yetkili değil" 403'e
    eşlenir, bkz. require_user)."""
    out = _mint(db)
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"baskasi@example.com"})
    with pytest.raises(PermissionError, match="Bu hesap yetkili değil"):
        auth_mod.verify_bearer_email(out["token"])


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
