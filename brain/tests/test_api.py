import pytest
from fastapi.testclient import TestClient

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


def test_chat_roundtrip(client):
    r = client.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:selam"


def test_chat_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    assert r.status_code in (401, 403)
