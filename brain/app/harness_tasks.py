"""Durable, versioned handoff ledger for external harness runs.

This collection is intentionally separate from the legacy Scheduler ``tasks``
collection. A dispatch claim is a one-way boundary: after it succeeds, a worker
must reconcile an uncertain harness submission using the task ID as its stable
handoff key where the harness supports one. It must not blindly repeat a side
effect.
"""

import copy
import hashlib
import json
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore


COLLECTION = "harness_tasks_v1"
SCHEMA_VERSION = 1

QUEUED = "queued"
CLAIMED = "claimed"
RUNNING = "running"
WAITING_USER = "waiting_user"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})

_ALLOWED = {
    QUEUED: frozenset({CLAIMED, CANCELLED}),
    CLAIMED: frozenset({RUNNING, FAILED, CANCELLED}),
    RUNNING: frozenset({WAITING_USER, COMPLETED, FAILED, CANCELLED}),
    WAITING_USER: frozenset({RUNNING, COMPLETED, FAILED, CANCELLED}),
}
_IMMUTABLE = ("schema_version", "task_id", "owner", "source", "event_id",
              "delivery_target", "harness_kind", "envelope")


class TaskConflict(ValueError):
    """The same source event ID was presented with different task content."""


class InvalidTransition(ValueError):
    """The requested state change is unsafe or outside the task lifecycle."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def task_id(source: str, event_id: str) -> str:
    """Stable Firestore-safe ID with unambiguous source/event encoding."""
    source = _required(source, "source")
    event_id = _required(event_id, "event_id")
    encoded = json.dumps([source, event_id], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return "h1_" + hashlib.sha256(encoded).hexdigest()


def create_once(db, *, owner: str, source: str, event_id: str,
                delivery_target: dict, envelope: dict, now_fn=_now) -> tuple[dict, bool]:
    """Atomically create one task per source event; reject changed duplicates.

    Returns ``(record, created)``. The envelope is the harness-neutral task
    payload; callers may include goal, context, authority and success criteria.
    Neither envelope nor delivery target can be changed through this module.
    """
    _required(owner, "owner")
    if not isinstance(delivery_target, dict) or not delivery_target:
        raise ValueError("delivery_target must be a nonempty dict")
    harness_kind = _required(delivery_target.get("kind"), "delivery_target.kind")
    if not isinstance(envelope, dict) or not envelope:
        raise ValueError("envelope must be a nonempty dict")
    key = task_id(source, event_id)
    immutable = {
        "schema_version": SCHEMA_VERSION,
        "task_id": key,
        "owner": owner,
        "source": source,
        "event_id": event_id,
        "delivery_target": copy.deepcopy(delivery_target),
        "harness_kind": harness_kind,
        "envelope": copy.deepcopy(envelope),
    }
    when = now_fn()
    record = {
        **immutable,
        "status": QUEUED,
        "created_at": when,
        "updated_at": when,
        "harness_run_id": None,
        "harness_session_id": None,
        "delivery_attempts": 0,
        "last_delivery_at": None,
        "waiting_for": None,
        "result": None,
    }
    ref = db.collection(COLLECTION).document(key)
    try:
        ref.create(record)
        return copy.deepcopy(record), True
    except AlreadyExists:
        snap = ref.get()
        if not snap.exists:
            # A concurrent delete is not a valid duplicate; never invent a new
            # task in this ambiguous state.
            raise TaskConflict("existing task disappeared during duplicate check")
        existing = snap.to_dict()
        if any(existing.get(field) != immutable[field] for field in _IMMUTABLE):
            raise TaskConflict("source event ID already belongs to another task payload")
        return existing, False


def get(db, key: str) -> dict | None:
    snap = db.collection(COLLECTION).document(_required(key, "task_id")).get()
    return snap.to_dict() if snap.exists else None


def _change(db, key: str, decide) -> dict:
    ref = db.collection(COLLECTION).document(_required(key, "task_id"))

    @firestore.transactional
    def apply(transaction):
        snap = next(transaction.get(ref))
        if not snap.exists:
            raise KeyError(key)
        record = snap.to_dict()
        updates = decide(record)
        if updates:
            transaction.set(ref, updates, merge=True)
            record.update(updates)
        return record

    return apply(db.transaction())


def claim_dispatch(db, key: str, *, now_fn=_now) -> tuple[dict, bool]:
    """Claim the only automatic harness submission attempt for this task.

    The caller must persist/reconcile an ambiguous result. A second worker
    receives ``claimed=False`` and must not dispatch again, even after restart.
    """
    claimed = False

    def decide(record):
        nonlocal claimed
        claimed = record["status"] == QUEUED
        if not claimed:
            return None
        when = now_fn()
        return {"status": CLAIMED, "updated_at": when,
                "delivery_attempts": 1, "last_delivery_at": when}

    record = _change(db, key, decide)
    return record, claimed


def record_harness_run(db, key: str, *, run_id: str, session_id: str | None = None,
                       now_fn=_now) -> dict:
    """Reserve the harness run identity and move CLAIMED to RUNNING.

    A CLI adapter may choose ``run_id=task_id`` before launching its process;
    RUNNING then means the invocation is claimed, not proof that the OS process
    has started. A session ID may be discovered later and recorded separately.
    ``None`` here means unspecified, so a replay without a session ID remains
    idempotent after the session has been filled.
    """
    _required(run_id, "run_id")
    if session_id is not None:
        _required(session_id, "session_id")

    def decide(record):
        if (record["harness_run_id"] == run_id and
                (session_id is None or record["harness_session_id"] == session_id)):
            return None
        if record["status"] != CLAIMED or record["harness_run_id"] or record["harness_session_id"]:
            raise InvalidTransition("harness run cannot be replaced or attached in this state")
        return {"status": RUNNING, "harness_run_id": run_id,
                "harness_session_id": session_id, "updated_at": now_fn()}

    return _change(db, key, decide)


def record_harness_session(db, key: str, session_id: str, *, now_fn=_now) -> dict:
    """Fill a previously unknown CLI session ID once, while the run is live."""
    _required(session_id, "session_id")

    def decide(record):
        if record["status"] not in (RUNNING, WAITING_USER) or not record["harness_run_id"]:
            raise InvalidTransition("session can be recorded only for an active harness run")
        existing = record["harness_session_id"]
        if existing == session_id:
            return None
        if existing is not None:
            raise TaskConflict("harness session ID already recorded differently")
        return {"harness_session_id": session_id, "updated_at": now_fn()}

    return _change(db, key, decide)


def transition(db, key: str, status: str, *, result: dict | None = None,
               waiting_for: str | None = None, now_fn=_now) -> dict:
    """Record a verified harness/user state, preserving terminal finality.

    A result is required for terminal states (an empty dict is allowed), and
    forbidden otherwise. Repeating an identical transition is harmless.
    """
    if status not in {WAITING_USER, RUNNING, COMPLETED, FAILED, CANCELLED}:
        raise ValueError("unsupported target status")
    if (status in TERMINAL) != (result is not None):
        raise ValueError("result is required exactly for terminal status")
    if result is not None and not isinstance(result, dict):
        raise ValueError("result must be a dict")
    if waiting_for is not None and (status != WAITING_USER or not isinstance(waiting_for, str)
                                    or not waiting_for.strip()):
        raise ValueError("waiting_for must be a nonempty question for waiting_user")
    final_result = copy.deepcopy(result)

    def decide(record):
        current = record["status"]
        if current == status:
            if status in TERMINAL and record["result"] != final_result:
                raise TaskConflict("terminal result already recorded differently")
            if status == WAITING_USER and waiting_for is not None and record.get("waiting_for") != waiting_for:
                raise TaskConflict("waiting request already recorded differently")
            return None
        if status not in _ALLOWED.get(current, ()):
            raise InvalidTransition(f"cannot change task from {current} to {status}")
        if status in (RUNNING, WAITING_USER) and not record["harness_run_id"]:
            raise InvalidTransition("harness run ID is required for this state")
        updates = {"status": status, "updated_at": now_fn()}
        if status == WAITING_USER:
            updates["waiting_for"] = waiting_for
        else:
            updates["waiting_for"] = None
        if status in TERMINAL:
            updates["result"] = final_result
        return updates

    return _change(db, key, decide)
