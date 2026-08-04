"""Wear W0 — cihaz token uçları (spec §4.2). test_approvals_api client deseni."""
import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.main as main_mod
from app import device_tokens
from app.auth import require_google_user
from app.memory import Memory
from app.messages import MessageStore
from tests.fakes import FakeDB

USER = "owner@example.com"


@pytest.fixture()
def db():
    return FakeDB()


@pytest.fixture()
def client(monkeypatch, db):
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", MessageStore(db))
    auth_mod.init(lambda: db)
    main_mod.app.dependency_overrides[require_google_user] = lambda: USER
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()
    auth_mod.init(None)


def test_endpoints_require_auth():
    with TestClient(main_mod.app) as c:
        assert c.post("/api/device-tokens", json={"device": "w"}).status_code in (401, 403)
        assert c.get("/api/device-tokens").status_code in (401, 403)
        assert c.delete("/api/device-tokens/x").status_code in (401, 403)


def test_a_device_token_cannot_manage_tokens(client, db):
    """Cihaz token'ı token basamaz/listeleyemez/iptal edemez (spec §4.2)."""
    out = device_tokens.mint(db, email=USER, device="watch-ultra")
    main_mod.app.dependency_overrides.clear()      # gerçek require_google_user koşsun
    headers = {"Authorization": f"Bearer {out['token']}"}
    assert client.post("/api/device-tokens", json={"device": "w"},
                       headers=headers).status_code == 403
    assert client.get("/api/device-tokens", headers=headers).status_code == 403
    assert client.delete(f"/api/device-tokens/{out['id']}",
                         headers=headers).status_code == 403


def test_mint_list_revoke_roundtrip(client, db):
    r = client.post("/api/device-tokens", json={"device": "watch-ultra"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"].startswith(device_tokens.PREFIX)
    assert body["device"] == "watch-ultra"

    listed = client.get("/api/device-tokens").json()["tokens"]
    assert [t["device"] for t in listed] == ["watch-ultra"]
    assert body["token"] not in str(listed)        # düz token listede YOK

    r2 = client.delete(f"/api/device-tokens/{body['id']}")
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert client.get("/api/device-tokens").json()["tokens"][0]["revoked"] is True


def test_minted_token_authenticates_chat_and_dies_on_revoke(client, db, monkeypatch):
    """ZİNCİR PİNİ (spec §10 W0): mint → require_user geçer → revoke → 401.
    /api/chat yerine daha ucuz bir require_user ucu kullanılır: /api/approvals."""
    body = client.post("/api/device-tokens", json={"device": "watch-ultra"}).json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/approvals", headers=headers).status_code == 200
    client.delete(f"/api/device-tokens/{body['id']}")
    assert client.get("/api/approvals", headers=headers).status_code == 401


def test_mint_rejects_a_blank_device(client):
    assert client.post("/api/device-tokens", json={"device": "  "}).status_code == 422


def test_cold_start_self_initializes_the_device_token_gate(monkeypatch, db):
    """C1: require_user (FastAPI dependency) koşar İÇİNDE _init'ten ÖNCE --
    soğuk başlangıçta tembel _init'e güvenemez. main_mod._memory=None + gerçek
    _init'i taklit eden fake ile "hiç istek gelmemiş" bir process simüle
    edilir. Dependency override YOK: gerçek require_user zinciri koşar, ve
    kapı main._device_token_db'nin kendisiyle açık kalmalı -- import zamanında
    kayıtlı olan provider, önceki testlerin auth_mod.init(None) teardown'ları
    tarafından ezilmiş olabileceğinden burada yeniden kaydedilir."""
    token = device_tokens.mint(db, email=USER, device="watch-ultra")["token"]

    def fake_init():
        main_mod._memory = Memory(db)

    monkeypatch.setattr(main_mod, "_memory", None)
    monkeypatch.setattr(main_mod, "_init", fake_init)
    auth_mod.init(main_mod._device_token_db)
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/approvals", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
    finally:
        auth_mod.init(None)


# -- 502 dalları: _init() başarılı, backing device_tokens çağrısı patlıyor --
# (test_api.py'deki test_conversations_returns_502_on_failure /
# test_delete_conversation_returns_502_on_failure deseninin aynısı.)

def _boom(*args, **kwargs):
    raise RuntimeError("firestore down")


def test_mint_returns_502_on_failure(client, monkeypatch):
    monkeypatch.setattr(device_tokens, "mint", _boom)
    r = client.post("/api/device-tokens", json={"device": "watch-ultra"})
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]


def test_list_returns_502_on_failure(client, monkeypatch):
    monkeypatch.setattr(device_tokens, "list_tokens", _boom)
    r = client.get("/api/device-tokens")
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]


def test_revoke_returns_502_on_failure(client, monkeypatch):
    monkeypatch.setattr(device_tokens, "revoke", _boom)
    r = client.delete("/api/device-tokens/some-id")
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]


def test_revoke_unknown_id_returns_404(client):
    """Minor 4: kayıt defterinde olmayan id 200 {ok:true} DEĞİL, 404 dönmeli."""
    r = client.delete("/api/device-tokens/olmayan-id")
    assert r.status_code == 404
    assert "bulunamadı" in r.json()["detail"].lower()
