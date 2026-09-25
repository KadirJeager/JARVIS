"""Google Chat delivery contract with synthetic signed-token verification."""

from fastapi.testclient import TestClient

from app import harness_control, harness_tasks as ledger
from tests.fakes import FakeDB


AUDIENCE = "https://control.example.test/v1/chat/events"
USER = "users/123456789"
CHAT_IDENTITY = {"email": "chat@system.gserviceaccount.com", "email_verified": True}


def _event(*, name="spaces/room/messages/message-1", text="Check the project",
           user=USER, kind="MESSAGE", thread="spaces/room/threads/thread-1"):
    event = {
        "type": kind,
        "user": {"name": user},
        "space": {"name": "spaces/room"},
        "message": {"name": name, "text": text, "argumentText": text},
    }
    if thread is not None:
        event["thread"] = {"name": thread}
    return event


def _client(*, claims=CHAT_IDENTITY, audience=AUDIENCE, allowed_user=USER,
            manager_kind="hermes"):
    db = FakeDB()
    observed = []

    def verify(token, expected_audience):
        observed.append((token, expected_audience))
        if token != "signed-chat-token":
            raise ValueError("bad signature or audience")
        return claims

    api = harness_control.create_app(
        db=db, owner="kadir", worker_token="worker-secret",
        ingest_token="ingest-secret", chat_audience=audience,
        chat_allowed_user=allowed_user, chat_manager_kind=manager_kind,
        chat_token_verifier=verify,
    )
    return TestClient(api), db, observed


def _post(http, event=None, token="signed-chat-token"):
    return http.post(
        "/v1/chat/events", json=_event() if event is None else event,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_chat_message_queues_once_and_keeps_reply_route():
    http, db, observed = _client()
    first = _post(http)
    assert first.status_code == 200
    assert observed == [("signed-chat-token", AUDIENCE)]
    task_id = ledger.task_id("google_chat", "MESSAGE:spaces/room/messages/message-1")
    assert first.json() == {"text": f"Alındı. Görev sıraya alındı: {task_id}"}
    record = ledger.get(db, task_id)
    assert record["owner"] == "kadir"
    assert record["status"] == ledger.QUEUED
    assert record["delivery_target"] == {
        "kind": "hermes", "device": "pc", "channel": "google_chat",
        "space": "spaces/room", "thread": "spaces/room/threads/thread-1",
    }
    assert record["envelope"]["goal"] == "Check the project"
    assert record["envelope"]["context"]["chat_user"] == USER

    replay = _post(http)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(db.collection(ledger.COLLECTION).docs) == 1


def test_chat_authentication_and_owner_allowlist():
    http, db, _ = _client()
    assert http.post("/v1/chat/events", json=_event()).status_code == 401
    assert _post(http, token="wrong").status_code == 401
    assert _post(http, _event(user="users/other")).status_code == 403
    assert db.collection(ledger.COLLECTION).docs == {}

    for claims in (
        {"email": "someone@example.com", "email_verified": True},
        {"email": "chat@system.gserviceaccount.com", "email_verified": False},
        {"email": "chat@system.gserviceaccount.com"},
    ):
        other, other_db, _ = _client(claims=claims)
        assert _post(other).status_code == 401
        assert other_db.collection(ledger.COLLECTION).docs == {}


def test_unsupported_events_have_no_task_side_effect():
    http, db, _ = _client()
    assert _post(http, _event(kind="ADDED_TO_SPACE")).json() == {}
    assert _post(http, _event(kind="CARD_CLICKED", user="users/other")).json() == {}
    assert db.collection(ledger.COLLECTION).docs == {}


def test_changed_duplicate_and_invalid_routes_fail_closed():
    http, db, _ = _client()
    assert _post(http).status_code == 200
    assert _post(http, _event(text="Different request")).status_code == 409
    assert _post(http, _event(name="spaces/another/messages/m1")).status_code == 422
    assert _post(http, _event(thread="spaces/another/threads/t1")).status_code == 422
    assert _post(http, _event(text=" ")).status_code == 422
    assert len(db.collection(ledger.COLLECTION).docs) == 1


def test_chat_ingress_requires_explicit_configuration():
    for options in (
        {"audience": None},
        {"audience": "http://insecure.example/v1/chat/events"},
        {"allowed_user": None},
        {"manager_kind": None},
    ):
        http, db, _ = _client(**options)
        assert _post(http).status_code == 503
        assert db.collection(ledger.COLLECTION).docs == {}


def test_direct_message_without_thread_is_preserved():
    http, db, _ = _client()
    event = _event(name="spaces/dm/messages/m1", thread=None)
    event["space"]["name"] = "spaces/dm"
    assert _post(http, event).status_code == 200
    task = next(iter(db.collection(ledger.COLLECTION).docs.values()))
    assert task["delivery_target"] == {
        "kind": "hermes", "device": "pc", "channel": "google_chat", "space": "spaces/dm",
    }


def test_cloud_status_answers_without_pc_manager_or_new_task():
    http, db, _ = _client(manager_kind=None)
    ledger.create_once(db, owner="kadir", source="manual", event_id="one",
                       delivery_target={"kind": "agy", "device": "pc"},
                       envelope={"goal": "pending"})
    ledger.create_once(db, owner="other", source="manual", event_id="two",
                       delivery_target={"kind": "agy", "device": "pc"},
                       envelope={"goal": "private"})
    before = len(db.collection(ledger.COLLECTION).docs)
    reply = _post(http, _event(text="durum"))
    assert reply.status_code == 200
    assert reply.json() == {"text": (
        "Görevler: 1 bekleyen, 0 yürüyen, 0 yanıt bekleyen. "
        "PC güç durumu henüz bağlı değil."
    )}
    assert len(db.collection(ledger.COLLECTION).docs) == before


def test_chat_task_is_pc_eligible_and_terminal_result_retries_delivery(monkeypatch):
    class Transaction:
        def get(self, ref):
            return iter((ref.get(),))

        def set(self, ref, data, merge=False):
            ref.set(data, merge=merge)

    class DB(FakeDB):
        def transaction(self):
            return Transaction()

    monkeypatch.setattr(ledger.firestore, "transactional", lambda fn: fn)
    db = DB()
    sends = []

    def send(record, text):
        sends.append((record["task_id"], text))
        if len(sends) == 1:
            raise RuntimeError("response lost after Chat accepted message")
        return {"name": "spaces/room/messages/reply-1"}

    api = TestClient(harness_control.create_app(
        db=db, owner="kadir", worker_token="worker-secret", ingest_token="ingest-secret",
        chat_audience=AUDIENCE, chat_allowed_user=USER, chat_manager_kind="agy",
        chat_token_verifier=lambda _token, _aud: CHAT_IDENTITY,
        chat_result_sender=send,
    ))
    created = _post(api)
    assert created.status_code == 200
    task = next(iter(db.collection(ledger.COLLECTION).docs.values()))
    assert task["delivery_target"]["device"] == "pc"
    key = task["task_id"]
    headers = {"Authorization": "Bearer worker-secret"}
    assert api.post(f"/v1/tasks/{key}/claim", headers=headers).json()["claimed"] is True
    assert api.post(f"/v1/tasks/{key}/outcome", headers=headers,
                    json={"status": "running", "run_id": key}).status_code == 200
    body = {"status": "completed", "run_id": key,
            "result": {"summary": "Kontrol edildi", "verified": True}}
    first = api.post(f"/v1/tasks/{key}/outcome", headers=headers, json=body)
    assert first.status_code == 503
    assert ledger.get(db, key)["status"] == ledger.COMPLETED
    retry = api.post(f"/v1/tasks/{key}/outcome", headers=headers, json=body)
    assert retry.status_code == 200
    assert sends == [(key, "Tamamlandı: Kontrol edildi")] * 2


def test_workspace_addon_message_and_status_use_addon_actions():
    db = FakeDB()
    service_account = "addon-chat@example.iam.gserviceaccount.com"
    api = TestClient(harness_control.create_app(
        db=db, owner="kadir", worker_token="worker-secret", ingest_token="ingest-secret",
        chat_audience=AUDIENCE, chat_allowed_user=USER, chat_manager_kind="hermes",
        chat_event_format="workspace_addon", chat_service_account=service_account,
        chat_token_verifier=lambda _token, _aud: {
            "email": service_account, "email_verified": True,
        },
    ))
    message = {
        "chat": {
            "user": {"name": USER},
            "messagePayload": {
                "message": {"name": "spaces/room/messages/addon-1", "text": "Check the project",
                            "thread": {"name": "spaces/room/threads/thread-1"}},
                "space": {"name": "spaces/room"},
            },
        },
    }
    first = _post(api, message)
    assert first.status_code == 200
    assert first.json()["hostAppDataAction"]["chatDataAction"]["createMessageAction"]["message"]["text"].startswith(
        "Alındı. Görev sıraya alındı: "
    )
    assert _post(api, message).json() == first.json()
    assert len(db.collection(ledger.COLLECTION).docs) == 1
    task = next(iter(db.collection(ledger.COLLECTION).docs.values()))
    assert task["delivery_target"]["thread"] == "spaces/room/threads/thread-1"
    assert task["envelope"]["goal"] == "Check the project"

    message["chat"]["messagePayload"]["message"]["name"] = "spaces/room/messages/addon-2"
    message["chat"]["messagePayload"]["message"]["text"] = "durum"
    status = _post(api, message)
    assert status.status_code == 200
    assert "Görevler: 1 bekleyen" in status.json()["hostAppDataAction"]["chatDataAction"]["createMessageAction"]["message"]["text"]
    assert len(db.collection(ledger.COLLECTION).docs) == 1


def test_workspace_addon_rejects_wrong_identity_and_user():
    db = FakeDB()
    api = TestClient(harness_control.create_app(
        db=db, owner="kadir", worker_token="worker-secret", ingest_token="ingest-secret",
        chat_audience=AUDIENCE, chat_allowed_user=USER, chat_manager_kind="hermes",
        chat_event_format="workspace_addon", chat_service_account="addon@example.com",
        chat_token_verifier=lambda _token, _aud: CHAT_IDENTITY,
    ))
    event = {"chat": {"user": {"name": USER}, "messagePayload": {
        "message": {"name": "spaces/room/messages/m1", "text": "Hi"},
        "space": {"name": "spaces/room"},
    }}}
    assert _post(api, event).status_code == 401
    assert db.collection(ledger.COLLECTION).docs == {}

    allowed = TestClient(harness_control.create_app(
        db=db, owner="kadir", worker_token="worker-secret", ingest_token="ingest-secret",
        chat_audience=AUDIENCE, chat_allowed_user=USER, chat_manager_kind="hermes",
        chat_event_format="workspace_addon", chat_service_account="addon@example.com",
        chat_token_verifier=lambda _token, _aud: {
            "email": "addon@example.com", "email_verified": True,
        },
    ))
    event["chat"]["user"]["name"] = "users/other"
    assert _post(allowed, event).status_code == 403
    assert _post(allowed, {"chat": {"addedToSpacePayload": {}}}).json() == {}
    assert db.collection(ledger.COLLECTION).docs == {}
