import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.main as main_mod
from app.auth import require_user


@pytest.fixture()
def client(monkeypatch):
    async def fake_reply(user_id, session_id, message):
        return f"echo:{message}"

    monkeypatch.setattr(main_mod, "run_turn", fake_reply)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}


def test_chat_roundtrip(client):
    r = client.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:selam"


def test_chat_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    assert r.status_code in (401, 403)


def test_chat_returns_502_on_run_turn_failure(monkeypatch):
    async def boom(user_id, session_id, message):
        raise RuntimeError("agent blew up")

    monkeypatch.setattr(main_mod, "run_turn", boom)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert r.json()["detail"] == (
        "Jarvis şu anda cevap veremiyor (altyapı hatası). Az sonra tekrar dene."
    )


def test_chat_invalid_token_returns_401(monkeypatch):
    def fake_verify(token, request, client_id):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        r = c.post(
            "/api/chat",
            json={"session_id": "s1", "message": "selam"},
            headers={"Authorization": "Bearer bogus"},
        )
    assert r.status_code == 401


def test_chat_non_allowlisted_email_returns_403(monkeypatch):
    def fake_verify(token, request, client_id):
        return {"email": "attacker@evil.com", "email_verified": True}

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        r = c.post(
            "/api/chat",
            json={"session_id": "s1", "message": "selam"},
            headers={"Authorization": "Bearer sometoken"},
        )
    assert r.status_code == 403


def test_chat_allowlisted_unverified_email_returns_403(monkeypatch):
    def fake_verify(token, request, client_id):
        return {"email": "owner@example.com", "email_verified": False}

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        r = c.post(
            "/api/chat",
            json={"session_id": "s1", "message": "selam"},
            headers={"Authorization": "Bearer sometoken"},
        )
    assert r.status_code == 403


def test_chat_allowlisted_verified_email_returns_200(monkeypatch):
    def fake_verify(token, request, client_id):
        return {"email": "owner@example.com", "email_verified": True}

    async def fake_reply(user_id, session_id, message):
        return f"echo:{message}"

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(main_mod, "run_turn", fake_reply)
    with TestClient(main_mod.app) as c:
        r = c.post(
            "/api/chat",
            json={"session_id": "s1", "message": "selam"},
            headers={"Authorization": "Bearer sometoken"},
        )
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:selam"
