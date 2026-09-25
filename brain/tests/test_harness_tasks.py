"""Harness ledger invariants against the project's Firestore FakeDB."""

from concurrent.futures import ThreadPoolExecutor
from threading import RLock

import pytest

from app import harness_tasks as ledger
from tests.fakes import FakeDB


class _FakeTransaction:
    def get(self, ref):
        return iter((ref.get(),))

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


class _TransactionalFakeDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.lock = RLock()

    def transaction(self):
        return _FakeTransaction()


@pytest.fixture
def db(monkeypatch):
    db = _TransactionalFakeDB()

    def transactional(callback):
        def run(tx):
            # The fake's lock models one atomic Firestore read/write commit.
            with db.lock:
                return callback(tx)
        return run

    monkeypatch.setattr(ledger.firestore, "transactional", transactional)
    return db


def _create(db, *, event_id="event-1", envelope=None, kind="codex_cli"):
    return ledger.create_once(
        db, owner="kadir", source="google-chat", event_id=event_id,
        delivery_target={"kind": kind, "device": "pc"},
        envelope=envelope or {"goal": "Read the project"}, now_fn=lambda: "t0",
    )


def test_stable_collision_safe_id_and_immutable_duplicate(db):
    assert ledger.task_id("a/b", "c") != ledger.task_id("a", "b/c")
    assert "/" not in ledger.task_id("a/b", "c")
    original, created = _create(db)
    duplicate, created_again = _create(db)
    assert created and not created_again
    assert duplicate == original
    assert original["schema_version"] == 1
    assert original["harness_kind"] == "codex_cli"
    assert original["harness_run_id"] is None
    assert original["harness_session_id"] is None
    assert original["task_id"] == ledger.task_id("google-chat", "event-1")
    assert ledger.COLLECTION != "tasks"
    assert "tasks" not in db.collections

    with pytest.raises(ledger.TaskConflict):
        _create(db, envelope={"goal": "Do something else"})
    with pytest.raises(ledger.TaskConflict):
        _create(db, kind="agy")


def test_input_mutation_does_not_change_saved_envelope(db):
    envelope = {"goal": "Read", "context": {"files": ["a"]}}
    saved, _ = _create(db, envelope=envelope)
    envelope["context"]["files"].append("b")
    assert saved["envelope"]["context"]["files"] == ["a"]
    assert ledger.get(db, saved["task_id"])["envelope"]["context"]["files"] == ["a"]


def test_concurrent_workers_cannot_double_dispatch(db):
    saved, _ = _create(db)
    key = saved["task_id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: ledger.claim_dispatch(db, key, now_fn=lambda: "t1"), range(20)))
    assert sum(claimed for _, claimed in claims) == 1
    persisted = ledger.get(db, key)
    assert persisted["status"] == ledger.CLAIMED
    assert persisted["delivery_attempts"] == 1
    assert persisted["last_delivery_at"] == "t1"
    # An uncertain submission is left for reconciliation, never auto-retried.
    _, claimed = ledger.claim_dispatch(db, key)
    assert not claimed


def test_reconcile_run_and_terminal_state_cannot_resurrect(db):
    saved, _ = _create(db)
    key = saved["task_id"]
    ledger.claim_dispatch(db, key)
    running = ledger.record_harness_run(db, key, run_id="run-7", session_id="session-3")
    assert running["status"] == ledger.RUNNING
    assert running["harness_kind"] == "codex_cli"
    assert running["harness_run_id"] == "run-7"
    assert running["harness_session_id"] == "session-3"
    assert ledger.record_harness_run(db, key, run_id="run-7", session_id="session-3") == running
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_run(db, key, run_id="run-8", session_id="session-3")
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_run(db, key, run_id="run-7", session_id="changed")

    ledger.transition(db, key, ledger.WAITING_USER)
    ledger.transition(db, key, ledger.RUNNING)
    done = ledger.transition(db, key, ledger.COMPLETED, result={"summary": "done"})
    assert ledger.transition(db, key, ledger.COMPLETED, result={"summary": "done"}) == done
    with pytest.raises(ledger.TaskConflict):
        ledger.transition(db, key, ledger.COMPLETED, result={"summary": "different"})
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(db, key, ledger.RUNNING)
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(db, key, ledger.FAILED, result={"error": "late"})
    assert ledger.get(db, key) == done


def test_dispatch_and_run_boundaries(db):
    saved, _ = _create(db)
    key = saved["task_id"]
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_run(db, key, run_id="r", session_id="s")
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(db, key, ledger.RUNNING)
    with pytest.raises(ValueError):
        ledger.transition(db, key, ledger.COMPLETED)
    with pytest.raises(KeyError):
        ledger.claim_dispatch(db, "missing")

    cancelled = ledger.transition(db, key, ledger.CANCELLED, result={"reason": "user"})
    assert cancelled["delivery_attempts"] == 0
    _, claimed = ledger.claim_dispatch(db, key)
    assert not claimed


def test_cli_run_without_session_is_stable(db):
    saved, _ = _create(db, kind="agy")
    key = saved["task_id"]
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_session(db, key, "session-before-run")
    ledger.claim_dispatch(db, key)
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_session(db, key, "session-before-run")
    # The CLI invocation reserves task_id as its stable run ID before spawn.
    running = ledger.record_harness_run(db, key, run_id=key)
    assert running["harness_kind"] == "agy"
    assert running["harness_run_id"] == key
    assert running["harness_session_id"] is None
    assert ledger.record_harness_run(db, key, run_id=key) == running
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_run(db, key, run_id=key, session_id="late-session")

    with pytest.raises(ValueError):
        ledger.record_harness_session(db, key, "")
    with pytest.raises(ValueError):
        ledger.record_harness_session(db, key, None)
    with_session = ledger.record_harness_session(db, key, "thread-42", now_fn=lambda: "t2")
    assert with_session["harness_session_id"] == "thread-42"
    assert with_session["updated_at"] == "t2"
    assert ledger.record_harness_session(db, key, "thread-42") == with_session
    # A replay with no session assertion remains idempotent after enrichment.
    assert ledger.record_harness_run(db, key, run_id=key) == with_session
    with pytest.raises(ledger.TaskConflict):
        ledger.record_harness_session(db, key, "thread-other")

    ledger.transition(db, key, ledger.WAITING_USER)
    assert ledger.record_harness_session(db, key, "thread-42")["status"] == ledger.WAITING_USER
    ledger.transition(db, key, ledger.COMPLETED, result={})
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_session(db, key, "thread-42")
    with pytest.raises(ledger.InvalidTransition):
        ledger.record_harness_session(db, key, "thread-other")


def test_concurrent_session_discovery_keeps_first_value(db):
    saved, _ = _create(db)
    key = saved["task_id"]
    ledger.claim_dispatch(db, key)
    ledger.record_harness_run(db, key, run_id=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda session: _try_session(db, key, session), ("session-a", "session-b")
        ))
    assert sorted(results) == ["conflict", "saved"]
    assert ledger.get(db, key)["harness_session_id"] in ("session-a", "session-b")


def _try_session(db, key, session):
    try:
        ledger.record_harness_session(db, key, session)
        return "saved"
    except ledger.TaskConflict:
        return "conflict"


def test_validation(db):
    with pytest.raises(ValueError):
        ledger.task_id("", "event")
    with pytest.raises(ValueError):
        ledger.task_id("chat", "")
    with pytest.raises(ValueError):
        ledger.create_once(db, owner="", source="chat", event_id="e",
                           delivery_target={"kind": "agy"}, envelope={"goal": "x"})
    with pytest.raises(ValueError):
        ledger.create_once(db, owner="kadir", source="chat", event_id="e",
                           delivery_target={"device": "pc"}, envelope={"goal": "x"})
