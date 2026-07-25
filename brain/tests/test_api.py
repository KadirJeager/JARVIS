import pytest
from fastapi.testclient import TestClient
from google.adk.flows.llm_flows.contents import _is_other_agent_reply
from google.adk.sessions import InMemorySessionService

import app.auth as auth_mod
import app.main as main_mod
from app.agent import AGENT_NAME
from app.auth import require_user
from app.conversations import ConversationStore
from app.messages import MessageStore
from tests.fakes import FakeDB, FakeRunner


def _store():
    # Monotonic ISO-ish timestamps so ordering is deterministic (no sleep).
    counter = iter(f"2026-01-01T00:00:{i:02d}.000000+00:00" for i in range(60))
    return MessageStore(FakeDB(), now_fn=lambda: next(counter))


def _conv_store():
    counter = iter(f"2026-01-01T00:00:{i:02d}.000000+00:00" for i in range(60))
    return ConversationStore(FakeDB(), now_fn=lambda: next(counter))


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


def test_history_returns_user_session_messages(monkeypatch):
    store = _store()
    store.append("owner@example.com", "s1", "user", "selam")
    store.append("owner@example.com", "s1", "model", "merhaba")
    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)  # skip Firestore/Runner init
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": "s1"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert [(m["role"], m["text"]) for m in r.json()["messages"]] == [
        ("user", "selam"), ("model", "merhaba")
    ]


def test_history_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.get("/api/history", params={"session_id": "s1"})
    assert r.status_code in (401, 403)


def test_history_rejects_bad_session_id(monkeypatch):
    monkeypatch.setattr(main_mod, "_messages", _store())
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": "a/b"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 400


def test_history_returns_502_on_init_failure(monkeypatch):
    """A cold-start Firestore/Runner init failure in /api/history must yield
    the same graceful Turkish 502 as a history-read failure, not a raw
    unhandled 500 (_init() used to run outside any try/except here)."""
    def boom():
        raise RuntimeError("firestore init blew up")

    monkeypatch.setattr(main_mod, "_init", boom)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": "s1"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert r.json()["detail"] == (
        "Jarvis şu anda geçmişi getiremiyor (altyapı hatası). Az sonra tekrar dene."
    )


def test_chat_rejects_bad_session_id():
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.post("/api/chat", json={"session_id": "a/b", "message": "selam"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 400


def test_history_cross_user_isolation(monkeypatch):
    """User A must only see their own messages, never user B's, even in the same session."""
    store = _store()
    user_a = "owner@example.com"
    user_b = "other@gmail.com"
    session_id = "s1"

    # Append messages for both users under the same session_id
    store.append(user_a, session_id, "user", "A's message")
    store.append(user_b, session_id, "user", "B's message")
    store.append(user_a, session_id, "model", "A's reply")

    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: user_a
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": session_id})
    finally:
        main_mod.app.dependency_overrides.clear()

    assert r.status_code == 200
    messages = r.json()["messages"]
    texts = [m["text"] for m in messages]
    assert "A's message" in texts
    assert "A's reply" in texts
    assert "B's message" not in texts


def test_history_response_includes_ts(monkeypatch):
    """Each message in the history response must include a 'ts' timestamp key."""
    store = _store()
    store.append("owner@example.com", "s1", "user", "selam")
    store.append("owner@example.com", "s1", "model", "merhaba")
    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": "s1"})
    finally:
        main_mod.app.dependency_overrides.clear()

    assert r.status_code == 200
    messages = r.json()["messages"]
    assert len(messages) == 2
    for msg in messages:
        assert "ts" in msg
        assert "role" in msg
        assert "text" in msg


@pytest.mark.asyncio
async def test_run_turn_persists_user_and_model_messages(monkeypatch):
    """run_turn must append user message and model reply to _messages."""
    store = _store()
    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_runner", FakeRunner(reply="merhaba"))
    monkeypatch.setattr(main_mod, "_init", lambda: None)

    async def fake_ensure(user_id, session_id):
        return None  # session object unused by FakeRunner

    monkeypatch.setattr(main_mod, "_ensure_session", fake_ensure)

    reply = await main_mod.run_turn("u@x.com", "s1", "selam")
    assert reply == "merhaba"
    hist = store.history("u@x.com", "s1")
    assert [(h["role"], h["text"]) for h in hist] == [("user", "selam"), ("model", "merhaba")]


@pytest.mark.asyncio
async def test_run_turn_does_not_persist_empty_model_reply(monkeypatch):
    """If run_async yields no final response, reply == "". That empty string
    must NOT be persisted as a "model" row: Gemini rejects empty text parts
    (INVALID_ARGUMENT) on replay, which would brick every turn in the
    session after a cold start (see _ensure_session rehydration)."""
    store = _store()
    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_runner", FakeRunner(reply=""))
    monkeypatch.setattr(main_mod, "_init", lambda: None)

    async def fake_ensure(user_id, session_id):
        return None  # session object unused by FakeRunner

    monkeypatch.setattr(main_mod, "_ensure_session", fake_ensure)

    reply = await main_mod.run_turn("u@x.com", "s1", "selam")
    assert reply == ""
    hist = store.history("u@x.com", "s1")
    assert [(h["role"], h["text"]) for h in hist] == [("user", "selam")]


@pytest.mark.asyncio
async def test_ensure_session_rehydrates_from_history(monkeypatch):
    store = _store()
    store.append("u@x.com", "s1", "user", "adim Kadir")
    store.append("u@x.com", "s1", "model", "memnun oldum Kadir")
    svc = InMemorySessionService()
    monkeypatch.setattr(main_mod, "_session_service", svc)
    monkeypatch.setattr(main_mod, "_messages", store)

    session = await main_mod._ensure_session("u@x.com", "s1")
    texts = [ev.content.parts[0].text for ev in session.events]
    assert texts == ["adim Kadir", "memnun oldum Kadir"]
    roles = [ev.content.role for ev in session.events]
    assert roles == ["user", "model"]

    # Event.author drives ADK's context-builder attribution and must be the
    # agent's own name for model turns (not the raw Gemini role "model"), or
    # ADK reframes the reply as a different agent's message on cold start --
    # see main._ensure_session for the full explanation.
    authors = [ev.author for ev in session.events]
    assert authors == ["user", AGENT_NAME]

    # Prove ADK itself won't reframe the rehydrated model event as a "for
    # context" third-party quote (the actual bug this fix closes).
    model_event = session.events[1]
    assert _is_other_agent_reply(AGENT_NAME, model_event) is False


@pytest.mark.asyncio
async def test_ensure_session_cold_start_with_no_prior_messages(monkeypatch):
    """First-ever turn for a session: history() is empty, so the rehydration
    loop runs zero times. Must still return a freshly created session with no
    events, and must not raise."""
    store = _store()
    svc = InMemorySessionService()
    monkeypatch.setattr(main_mod, "_session_service", svc)
    monkeypatch.setattr(main_mod, "_messages", store)

    session = await main_mod._ensure_session("u@x.com", "brand-new-session")
    assert session.events == []


@pytest.mark.asyncio
async def test_ensure_session_existing_is_not_rehydrated(monkeypatch):
    store = _store()
    store.append("u@x.com", "s1", "user", "eski")
    svc = InMemorySessionService()
    await svc.create_session(app_name=main_mod.APP_NAME, user_id="u@x.com", session_id="s1")
    monkeypatch.setattr(main_mod, "_session_service", svc)
    monkeypatch.setattr(main_mod, "_messages", store)

    session = await main_mod._ensure_session("u@x.com", "s1")
    assert session.events == []  # already existed -> not rehydrated from history


# --- GET /api/conversations, DELETE /api/conversations/{session_id} --------
# (Katman 2b: enumerate + reopen/delete a user's previous conversations)


def test_conversations_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.get("/api/conversations")
    assert r.status_code in (401, 403)


def test_conversations_lists_user_conversations_newest_first(monkeypatch):
    conv_store = _conv_store()
    conv_store.touch("owner@example.com", "s1", "user", "eski konusma")
    conv_store.touch("owner@example.com", "s2", "user", "yeni konusma")
    monkeypatch.setattr(main_mod, "_conversations", conv_store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/conversations")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 200
    convs = r.json()["conversations"]
    assert [c["session_id"] for c in convs] == ["s2", "s1"]
    assert convs[0]["title"] == "yeni konusma"
    assert set(convs[0].keys()) == {"session_id", "title", "last_ts", "message_count"}


def test_conversations_cross_user_isolation(monkeypatch):
    """HARD CONSTRAINT: a user must never see another user's conversations."""
    conv_store = _conv_store()
    conv_store.touch("owner@example.com", "s1", "user", "benim konusmam")
    conv_store.touch("other@gmail.com", "s1", "user", "baskasinin konusmasi")
    monkeypatch.setattr(main_mod, "_conversations", conv_store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/conversations")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 200
    convs = r.json()["conversations"]
    assert [c["session_id"] for c in convs] == ["s1"]
    assert convs[0]["title"] == "benim konusmam"


def test_conversations_returns_502_on_failure(monkeypatch):
    monkeypatch.setattr(main_mod, "_init", lambda: None)

    class Boom:
        def list_conversations(self, user_id):
            raise RuntimeError("firestore down")

    monkeypatch.setattr(main_mod, "_conversations", Boom())
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/conversations")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]


def test_delete_conversation_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.delete("/api/conversations/s1")
    assert r.status_code in (401, 403)


def test_delete_conversation_removes_summary_and_messages(monkeypatch):
    conv_store = _conv_store()
    msg_store = _store()
    conv_store.touch("owner@example.com", "s1", "user", "silinecek")
    msg_store.append("owner@example.com", "s1", "user", "silinecek")
    monkeypatch.setattr(main_mod, "_conversations", conv_store)
    monkeypatch.setattr(main_mod, "_messages", msg_store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.delete("/api/conversations/s1")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json() == {"deleted": "s1"}
    assert conv_store.list_conversations("owner@example.com") == []
    assert msg_store.history("owner@example.com", "s1") == []


def test_delete_conversation_cannot_touch_another_users_conversation(monkeypatch):
    """HARD CONSTRAINT: a user must never be able to delete another user's
    conversation, even with the same session_id."""
    conv_store = _conv_store()
    msg_store = _store()
    conv_store.touch("owner@example.com", "s1", "user", "A nin konusmasi")
    conv_store.touch("other@gmail.com", "s1", "user", "B nin konusmasi")
    msg_store.append("other@gmail.com", "s1", "user", "B nin mesaji")
    monkeypatch.setattr(main_mod, "_conversations", conv_store)
    monkeypatch.setattr(main_mod, "_messages", msg_store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.delete("/api/conversations/s1")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 200
    # Attacker's own (empty) conversation is gone, but victim's survives intact.
    assert [c["title"] for c in conv_store.list_conversations("other@gmail.com")] == ["B nin konusmasi"]
    assert [h["text"] for h in msg_store.history("other@gmail.com", "s1")] == ["B nin mesaji"]


def test_delete_conversation_rejects_bad_session_id(monkeypatch):
    monkeypatch.setattr(main_mod, "_conversations", _conv_store())
    monkeypatch.setattr(main_mod, "_messages", _store())
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            # "/" can't appear here (it would split into two path segments and
            # 405 at routing, before sanitize_session_id even runs) -- an
            # overlong id exercises the same rejection without that pitfall.
            r = c.delete(f"/api/conversations/{'x' * 300}")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 400


def test_delete_conversation_returns_502_on_failure(monkeypatch):
    monkeypatch.setattr(main_mod, "_init", lambda: None)

    class Boom:
        def delete(self, user_id, session_id):
            raise RuntimeError("firestore down")

    monkeypatch.setattr(main_mod, "_conversations", Boom())
    monkeypatch.setattr(main_mod, "_messages", _store())
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.delete("/api/conversations/s1")
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]
