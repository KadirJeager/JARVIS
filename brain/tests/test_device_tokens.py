"""Wear W0 — cihaz token kayıt defteri (spec §4).

Taşıyıcı pimler:
- Düz token Firestore'a ASLA yazılmaz (yalnız SHA-256 doc id).
- verify fail-closed: yok/revoked/süresi geçmiş/DB hatası → PermissionError,
  mesaj Google yoluyla AYNI ("Geçersiz oturum") — şema sızıntısı yok.
- Kayar süre: her başarılı doğrulama expires_at'i İLERLETİR.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app import device_tokens
from tests.fakes import FakeDB

EMAIL = "owner@example.com"
T0 = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)


def _now_at(dt):
    return lambda: dt.isoformat()


def _mint(db, *, at=T0, device="watch-ultra"):
    return device_tokens.mint(db, email=EMAIL, device=device, now_fn=_now_at(at))


def test_mint_returns_plain_token_and_stores_only_the_hash():
    db = FakeDB()
    out = _mint(db)
    assert out["token"].startswith(device_tokens.PREFIX)
    expected_id = hashlib.sha256(out["token"].encode()).hexdigest()
    assert out["id"] == expected_id
    docs = db.collection(device_tokens.COLLECTION).docs
    assert list(docs) == [expected_id]
    stored = str(docs[expected_id])
    assert out["token"] not in stored          # düz token dokümanın HİÇBİR alanında yok
    assert docs[expected_id]["email"] == EMAIL
    assert docs[expected_id]["device"] == "watch-ultra"
    assert docs[expected_id]["revoked"] is False


def test_mint_sets_sliding_expiry_30_days_out():
    db = FakeDB()
    out = _mint(db)
    doc = db.collection(device_tokens.COLLECTION).docs[out["id"]]
    assert doc["expires_at"] == (T0 + timedelta(days=30)).isoformat()
    assert out["expires_at"] == doc["expires_at"]


def test_verify_returns_email_and_slides_the_expiry():
    db = FakeDB()
    out = _mint(db)
    later = T0 + timedelta(days=10)
    email = device_tokens.verify(db, out["token"], now_fn=_now_at(later))
    assert email == EMAIL
    doc = db.collection(device_tokens.COLLECTION).docs[out["id"]]
    assert doc["last_used_at"] == later.isoformat()
    assert doc["expires_at"] == (later + timedelta(days=30)).isoformat()  # İLERLEDİ


def test_verify_rejects_unknown_revoked_and_expired():
    db = FakeDB()
    out = _mint(db)

    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, "jdt_uydurma", now_fn=_now_at(T0))

    device_tokens.revoke(db, out["id"], now_fn=_now_at(T0))
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, out["token"], now_fn=_now_at(T0))

    out2 = _mint(db)
    stale = T0 + timedelta(days=31)
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, out2["token"], now_fn=_now_at(stale))


def test_verify_is_fail_closed_on_db_error():
    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(BoomDB(), "jdt_herhangi", now_fn=_now_at(T0))


def test_verify_still_passes_when_the_sliding_update_write_fails(caplog):
    """Kayar güncelleme düşerse doğrulama GEÇER ama WARNING şart (spec §4.3)."""
    db = FakeDB()
    out = _mint(db)

    real_collection = db.collection

    class ReadOnlyDoc:
        def __init__(self, inner):
            self._inner = inner

        def get(self):
            return self._inner.get()

        def set(self, *a, **k):
            raise RuntimeError("write denied")

    class Wrapper:
        def __init__(self, col):
            self._col = col

        def collection(self, name):
            return self

        def document(self, key):
            return ReadOnlyDoc(self._col.document(key))

    email = device_tokens.verify(
        Wrapper(real_collection(device_tokens.COLLECTION)), out["token"],
        now_fn=_now_at(T0 + timedelta(days=1)))
    assert email == EMAIL
    assert any("kayar" in r.message.lower() or "slid" in r.message.lower()
               or "güncelle" in r.message.lower()
               for r in caplog.records if r.levelname == "WARNING")


def test_expired_boundary_is_closed():
    """expires_at == now TAM ANINDA token ölüdür (approvals._is_expired kuralı)."""
    db = FakeDB()
    out = _mint(db)
    exactly = T0 + timedelta(days=30)
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, out["token"], now_fn=_now_at(exactly))


def test_list_tokens_never_contains_plain_tokens():
    db = FakeDB()
    out = _mint(db)
    _mint(db, device="tablet")
    rows = device_tokens.list_tokens(db)
    assert len(rows) == 2
    joined = str(rows)
    assert out["token"] not in joined
    assert {r["device"] for r in rows} == {"watch-ultra", "tablet"}
    assert all(set(r) >= {"id", "device", "created_at", "last_used_at",
                          "expires_at", "revoked"} for r in rows)


def test_revoke_stamps_without_deleting_and_unknown_is_observation():
    db = FakeDB()
    out = _mint(db)
    msg = device_tokens.revoke(db, out["id"], now_fn=_now_at(T0))
    doc = db.collection(device_tokens.COLLECTION).docs[out["id"]]
    assert doc["revoked"] is True and doc["revoked_at"] == T0.isoformat()
    assert "iptal" in msg.lower()
    assert "yok" in device_tokens.revoke(db, "olmayan-id", now_fn=_now_at(T0)).lower()
