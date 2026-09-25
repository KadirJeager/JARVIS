"""Standalone control API contract with synthetic credentials and Firestore."""

import httpx
from fastapi.testclient import TestClient
import pytest

from app import harness_control, harness_tasks as ledger, hermes_model_api
from tests.fakes import FakeDB


class _Transaction:
    def get(self, ref):
        return iter((ref.get(),))

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


class _DB(FakeDB):
    def transaction(self):
        return _Transaction()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ledger.firestore, "transactional", lambda fn: fn)
    db = _DB()
    api = harness_control.create_app(db=db, owner="kadir",
                                     worker_token="worker-secret",
                                     ingest_token="ingest-secret")
    return TestClient(api), db


def _headers(role):
    return {"Authorization": f"Bearer {role}-secret"}


def _body(event="e-1"):
    return {"source": "manual", "event_id": event,
            "delivery_target": {"kind": "codex", "device": "pc"},
            "envelope": {"goal": "Check marker"}}


def test_ingest_auth_owner_and_idempotency(client):
    http, db = client
    assert http.post("/v1/tasks", json=_body()).status_code == 401
    assert http.post("/v1/tasks", headers=_headers("worker"), json=_body()).status_code == 401
    assert http.post("/v1/tasks", headers=_headers("ingest"),
                     json={**_body(), "owner": "other"}).status_code == 422

    first = http.post("/v1/tasks", headers=_headers("ingest"), json=_body())
    assert first.status_code == 201
    task = first.json()["task"]
    assert task["owner"] == "kadir"
    assert task["status"] == ledger.QUEUED
    assert first.json()["created"] is True
    replay = http.post("/v1/tasks", headers=_headers("ingest"), json=_body())
    assert replay.status_code == 200
    assert replay.json() == {"task": task, "created": False}
    changed = {**_body(), "envelope": {"goal": "Different"}}
    assert http.post("/v1/tasks", headers=_headers("ingest"), json=changed).status_code == 409
    assert ledger.get(db, task["task_id"]) == task


def test_worker_queue_scope_claim_and_result(client):
    http, db = client
    task = http.post("/v1/tasks", headers=_headers("ingest"), json=_body()).json()["task"]
    key = task["task_id"]
    other, _ = ledger.create_once(
        db, owner="other", source="manual", event_id="other-event",
        delivery_target={"kind": "agy", "device": "pc"}, envelope={"goal": "Private"}
    )
    assert http.get("/v1/tasks/queued", headers=_headers("ingest")).status_code == 401
    queue = http.get("/v1/tasks/queued", headers=_headers("worker"))
    assert queue.status_code == 200
    assert queue.json() == {"tasks": [task]}
    assert http.get(f"/v1/tasks/{other['task_id']}", headers=_headers("worker")).status_code == 404
    assert http.post(f"/v1/tasks/{other['task_id']}/claim",
                     headers=_headers("worker")).status_code == 404
    assert http.post(f"/v1/tasks/{other['task_id']}/outcome",
                     headers=_headers("worker"),
                     json={"status": "failed", "result": {}}).status_code == 404
    assert http.get(f"/v1/tasks/{key}", headers=_headers("ingest")).status_code == 401

    claimed = http.post(f"/v1/tasks/{key}/claim", headers=_headers("worker"))
    assert claimed.status_code == 200
    assert claimed.json()["claimed"] is True
    assert claimed.json()["task"]["status"] == ledger.CLAIMED
    repeated = http.post(f"/v1/tasks/{key}/claim", headers=_headers("worker"))
    assert repeated.json()["claimed"] is False
    assert repeated.json()["task"]["delivery_attempts"] == 1
    assert http.get("/v1/tasks/queued", headers=_headers("worker")).json() == {"tasks": []}

    endpoint = f"/v1/tasks/{key}/outcome"
    assert http.post(endpoint, headers=_headers("worker"),
                     json={"status": "completed", "result": {}}).status_code == 409
    started = {"status": "running", "run_id": key}
    assert http.post(endpoint, headers=_headers("worker"), json=started).json()["task"]["status"] == ledger.RUNNING
    assert http.post(endpoint, headers=_headers("worker"), json=started).status_code == 200
    assert http.post(endpoint, headers=_headers("worker"),
                     json={"status": "running", "run_id": "wrong"}).status_code == 409
    done = {"status": "completed", "run_id": key,
            "result": {"summary": "marker checked", "verified": True}}
    completed = http.post(endpoint, headers=_headers("worker"), json=done)
    assert completed.status_code == 200
    assert completed.json()["task"]["status"] == ledger.COMPLETED
    assert http.post(endpoint, headers=_headers("worker"), json=done).json() == completed.json()
    assert http.post(endpoint, headers=_headers("worker"),
                     json={**done, "result": {"summary": "different"}}).status_code == 409
    assert http.post(endpoint, headers=_headers("worker"), json=started).status_code == 409


def test_outcome_validation_and_waiting_user(client):
    http, _ = client
    task = http.post("/v1/tasks", headers=_headers("ingest"), json=_body()).json()["task"]
    key = task["task_id"]
    endpoint = f"/v1/tasks/{key}/outcome"
    http.post(f"/v1/tasks/{key}/claim", headers=_headers("worker"))
    for invalid in (
        {"status": "running"},
        {"status": "completed"},
        {"status": "running", "run_id": key, "result": {}},
        {"status": "waiting_user", "run_id": key},
        {"status": "running", "run_id": key, "waiting_for": "question"},
        {"status": "flying", "run_id": key},
    ):
        assert http.post(endpoint, headers=_headers("worker"), json=invalid).status_code == 422
    assert http.post(endpoint, headers=_headers("worker"),
                     json={"status": "running", "run_id": key}).status_code == 200
    waiting = {"status": "waiting_user", "run_id": key,
               "waiting_for": "Which repo?"}
    assert http.post(endpoint, headers=_headers("worker"), json=waiting).json()["task"]["waiting_for"] == "Which repo?"
    assert http.post(endpoint, headers=_headers("worker"), json=waiting).status_code == 200
    resumed = http.post(endpoint, headers=_headers("worker"),
                        json={"status": "running", "run_id": key, "session_id": "s-1"})
    assert resumed.status_code == 200
    assert resumed.json()["task"]["status"] == ledger.RUNNING
    assert resumed.json()["task"]["harness_session_id"] == "s-1"


def test_terminal_replay_with_late_session_is_idempotent(client):
    http, _ = client
    task = http.post("/v1/tasks", headers=_headers("ingest"),
                     json=_body()).json()["task"]
    key = task["task_id"]
    endpoint = f"/v1/tasks/{key}/outcome"
    http.post(f"/v1/tasks/{key}/claim", headers=_headers("worker"))
    http.post(endpoint, headers=_headers("worker"),
              json={"status": "running", "run_id": key})
    terminal = {"status": "completed", "run_id": key, "session_id": "s-2",
                "result": {"summary": "done"}}
    first = http.post(endpoint, headers=_headers("worker"), json=terminal)
    assert first.status_code == 200
    assert first.json()["task"]["harness_session_id"] == "s-2"
    replay = http.post(endpoint, headers=_headers("worker"), json=terminal)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert http.post(endpoint, headers=_headers("worker"),
                     json={**terminal, "session_id": "other"}).status_code == 409


def test_missing_or_shared_tokens_fail_closed():
    for worker, ingest in (("", "ingest"), ("shared", "shared")):
        api = harness_control.create_app(db=_DB(), owner="kadir", worker_token=worker,
                                         ingest_token=ingest)
        with TestClient(api) as http:
            assert http.get("/v1/tasks/queued", headers={"Authorization": "Bearer shared"}).status_code == 503


def test_thin_control_app_exposes_hermes_model_boundary_without_adk(monkeypatch):
    monkeypatch.setenv("JARVIS_HERMES_TOKEN", "hermes-secret")
    monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_BASE_URL", "http://localhost:8317")
    monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_KEY", "proxy-secret")
    monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_MODEL", "gemini-test-flash")
    seen = []

    def upstream(req):
        seen.append((str(req.url), req.headers.get("authorization"), req.read()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(hermes_model_api, "_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(upstream), timeout=5))
    api = harness_control.create_app(db=_DB(), owner="kadir",
                                     worker_token="worker-secret",
                                     ingest_token="ingest-secret")
    with TestClient(api) as http:
        unauthorized = http.post("/v1/chat/completions", json={
            "model": "jarvis-gemini", "messages": [{"role": "user", "content": "OK"}]})
        assert unauthorized.status_code == 401
        result = http.post("/v1/chat/completions", json={
            "model": "jarvis-gemini", "messages": [{"role": "user", "content": "OK"}]},
            headers={"Authorization": "Bearer hermes-secret"})
    assert result.status_code == 200
    assert result.json()["choices"][0]["message"]["content"] == "OK"
    assert len(seen) == 1
    assert seen[0][0] == "http://localhost:8317/v1/chat/completions"
    assert seen[0][1] == "Bearer proxy-secret"
    assert b'"model":"gemini-test-flash"' in seen[0][2]
