"""Harness ledger and control API against a real Firestore emulator.

The other harness tests use FakeDB, whose transactions and ``create()`` only
model Firestore. These tests run the same code against the Firestore emulator
so transaction retries, ``AlreadyExists`` on ``create()`` and the status query
are Firestore's own behaviour. They are skipped unless FIRESTORE_EMULATOR_HOST
is set, e.g.::

    firebase emulators:start --only firestore  # or the emulator jar
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 pytest tests/test_harness_firestore_emulator.py
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import harness_control, harness_tasks as ledger
from app.pc_connector import poll_once

pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="needs a Firestore emulator (FIRESTORE_EMULATOR_HOST)",
)


@pytest.fixture
def db(monkeypatch):
    from google.cloud import firestore

    # A fresh collection per test keeps runs independent without deleting data.
    monkeypatch.setattr(ledger, "COLLECTION", f"harness_tasks_test_{uuid.uuid4().hex}")
    return firestore.Client(project="jarvis-emulator-test")


def _create(db, event_id="event-1", goal="Read the project"):
    return ledger.create_once(
        db, owner="kadir", source="google_chat", event_id=event_id,
        delivery_target={"kind": "hermes", "device": "pc"},
        envelope={"goal": goal},
    )


def test_create_once_is_idempotent_and_rejects_changed_payload(db):
    record, created = _create(db)
    again, created_again = _create(db)
    assert created and not created_again
    assert again == record
    with pytest.raises(ledger.TaskConflict):
        _create(db, goal="Something else")
    assert ledger.get(db, record["task_id"])["envelope"] == {"goal": "Read the project"}


def test_lifecycle_and_terminal_finality(db):
    key = _create(db)[0]["task_id"]
    record, claimed = ledger.claim_dispatch(db, key)
    assert claimed and record["status"] == ledger.CLAIMED
    assert ledger.claim_dispatch(db, key)[1] is False
    ledger.record_harness_run(db, key, run_id="run-1")
    ledger.record_harness_session(db, key, "session-1")
    ledger.transition(db, key, ledger.WAITING_USER, waiting_for="Which branch?")
    ledger.transition(db, key, ledger.RUNNING)
    done = ledger.transition(db, key, ledger.COMPLETED, result={"summary": "ok"})
    assert done["status"] == ledger.COMPLETED and done["waiting_for"] is None
    stored = ledger.get(db, key)
    assert stored["harness_session_id"] == "session-1"
    assert stored["result"] == {"summary": "ok"}
    with pytest.raises(ledger.TaskConflict):
        ledger.transition(db, key, ledger.COMPLETED, result={"summary": "changed"})
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(db, key, ledger.RUNNING)


def test_concurrent_claims_dispatch_exactly_once(db):
    key = _create(db)[0]["task_id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ledger.claim_dispatch(db, key)[1], range(8)))
    assert results.count(True) == 1
    assert ledger.get(db, key)["delivery_attempts"] == 1


def test_concurrent_creates_make_one_task(db):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _create(db)[1], range(8)))
    assert results.count(True) == 1


class _LocalHTTP:
    def __init__(self, api):
        self.api = api

    def request(self, method, url, *, headers, json_body=None, timeout=15):
        response = self.api.request(method, url.removeprefix("https://control.example"),
                                    headers=headers, json=json_body)
        response.raise_for_status()
        return response.json()


def test_control_api_and_pc_connector_complete_one_task(db, tmp_path):
    api = TestClient(harness_control.create_app(
        db=db, owner="kadir", worker_token="worker", ingest_token="ingest",
    ))
    body = {"source": "manual", "event_id": "emulator-event",
            "delivery_target": {"kind": "codex", "device": "pc"},
            "envelope": {"goal": "Report OK"}}
    first = api.post("/v1/tasks", json=body, headers={"Authorization": "Bearer ingest"})
    assert first.status_code == 201
    key = first.json()["task"]["task_id"]
    queued = api.get("/v1/tasks/queued", headers={"Authorization": "Bearer worker"})
    assert [task["task_id"] for task in queued.json()["tasks"]] == [key]

    calls = []

    def manager(task):
        calls.append(task["task_id"])
        return {"status": "completed", "result": {"summary": "OK", "verified": True}}

    options = dict(base_url="https://control.example", manager_kind="codex",
                   journal_path=tmp_path / "journal.sqlite3", run_manager=manager,
                   worker_token="worker", client=_LocalHTTP(api))
    assert poll_once(**options).launched == 1
    assert poll_once(**options).launched == 0
    assert calls == [key]
    assert ledger.get(db, key)["status"] == ledger.COMPLETED
    duplicate = api.post("/v1/tasks", json=body, headers={"Authorization": "Bearer ingest"})
    assert duplicate.status_code == 200 and duplicate.json()["created"] is False
