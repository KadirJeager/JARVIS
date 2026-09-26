"""Durable turn ledger and conversation heads in Firestore.

A turn is one user message and the assistant work it causes. It follows the
pattern of the old service's `brain/app/harness_tasks.py` (removed; see git
commit d36bd4d): a stable id derived from the event,
atomic create-once with an immutable-field check, and every state change
decided inside a transaction against an allowed-transition table.

Additions for the event-driven core:

- Turns of one conversation run strictly in submit order and one at a time.
  Submitting assigns `seq`; a turn can be claimed only when every earlier
  turn has settled (completed, failed, cancelled or awaiting reconciliation).
- A claim takes a lease. A crashed worker's turn becomes claimable again when
  the lease expires, as a new attempt with its own `run_id`
  (`<turn_id>.<attempt>`), because Harness `StepPersistence` run ids are
  single-shot. The worker decides from the previous attempt's recorded facts
  whether it may continue; uncertain side effects end in `reconciling`.
- Completing a turn moves the conversation head to the successful run, whose
  final snapshot is the history for the next turn.
- The owner may cancel a turn. A queued turn is cancelled at once, even when
  it waits behind earlier turns: `settled_seq` then advances over it only
  once every earlier turn has settled (`settled_ahead` holds the gap). A
  running turn gets `cancel_requested_at`; its worker stops the run and
  settles it, or leaves it for reconciliation when a tool may have acted.
- While a turn runs, the worker records its tool calls in `activity`; the
  reply or error message keeps that list so the conversation shows what the
  assistant did.

Collections: `turns/{turn_id}`, `users/{uid}/conversations/{cid}` and
`users/{uid}/conversations/{cid}/messages/{turn_id}.{role}`. Messages carry
`position` (`2*seq` for the user message, `2*seq+1` for the reply, error or
notice) so a client orders a conversation with one single-field index.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from google.cloud.firestore import AsyncClient, AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

from jarvis_core._firestore import TRANSACTION_ATTEMPTS

SCHEMA_VERSION = 1

QUEUED = 'queued'
RUNNING = 'running'
COMPLETED = 'completed'
FAILED = 'failed'
CANCELLED = 'cancelled'
RECONCILING = 'reconciling'
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})
SETTLED = TERMINAL | {RECONCILING}

_ALLOWED = {
    QUEUED: frozenset({RUNNING, CANCELLED}),
    RUNNING: frozenset({QUEUED, COMPLETED, FAILED, CANCELLED, RECONCILING}),
    RECONCILING: frozenset({COMPLETED, FAILED, CANCELLED}),
}
_IMMUTABLE = ('schema_version', 'turn_id', 'uid', 'conversation_id', 'client_message_id', 'text')
_TITLE_CHARS = 80
_CANCELLED_NOTICE = {'role': 'notice', 'type': 'cancelled'}
# Firestore caps one batched write at 500 operations.
_BATCH_OPS = 500


class TurnConflict(ValueError):
    """The same client message id was presented with different content, or a result differs."""


class InvalidTransition(ValueError):
    """The requested change is outside the turn lifecycle or from a stale attempt."""


class ConversationBusy(RuntimeError):
    """The conversation has a queued or running turn, so it cannot be deleted yet."""


def _settle_seq(conversation: dict[str, Any], seq: int) -> dict[str, Any]:
    """Conversation fields after turn `seq` settles.

    `settled_seq` only advances over a contiguous run of settled turns, so a
    turn cancelled while earlier ones are still open never lets a later turn
    start before them.
    """
    settled = int(conversation.get('settled_seq', 0))
    ahead = {int(value) for value in conversation.get('settled_ahead', [])}
    if seq > settled:
        ahead.add(seq)
    while settled + 1 in ahead:
        settled += 1
        ahead.discard(settled)
    return {'settled_seq': settled, 'settled_ahead': sorted(ahead)}


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


def turn_id(uid: str, conversation_id: str, client_message_id: str) -> str:
    """Stable Firestore-safe id with an unambiguous encoding of its parts."""
    encoded = json.dumps(
        [_required(uid, 'uid'), _required(conversation_id, 'conversation_id'),
         _required(client_message_id, 'client_message_id')],
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return 't1_' + hashlib.sha256(encoded).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Claim:
    """A leased attempt. `previous_run_id` is set when an earlier attempt of this turn ran."""

    turn: dict[str, Any]
    attempt: int
    run_id: str
    previous_run_id: str | None
    head_run_id: str | None


class FirestoreTurnStore:
    def __init__(self, client: AsyncClient, *, now: Callable[[], datetime] = _now) -> None:
        self._client = client
        self._turns = client.collection('turns')
        self._users = client.collection('users')
        self._now = now

    def _conversation(self, uid: str, conversation_id: str):  # noqa: ANN202 - Firestore reference type
        return self._users.document(uid).collection('conversations').document(conversation_id)

    def _message(self, turn: dict[str, Any], role: str):  # noqa: ANN202 - Firestore reference type
        return (
            self._conversation(turn['uid'], turn['conversation_id'])
            .collection('messages')
            .document(f"{turn['turn_id']}.{role}")
        )

    async def mark_dispatched(self, key: str) -> None:
        await self._turns.document(_required(key, 'turn_id')).update({'dispatched_at': self._now()})

    async def stale(self, *, dispatched_before: datetime, limit: int = 100) -> list[dict[str, Any]]:
        """Turns needing a new delivery: queued and not dispatched since `dispatched_before`, or with an expired lease."""
        queued = (
            self._turns.where(filter=FieldFilter('status', '==', QUEUED))
            .where(filter=FieldFilter('dispatched_at', '<', dispatched_before))
            .order_by('dispatched_at')
            .limit(limit)
        )
        expired = (
            self._turns.where(filter=FieldFilter('status', '==', RUNNING))
            .where(filter=FieldFilter('lease_expires_at', '<', self._now()))
            .order_by('lease_expires_at')
            .limit(limit)
        )
        return [snapshot.to_dict() or {} for query in (queued, expired) async for snapshot in query.stream()]

    async def get(self, key: str) -> dict[str, Any] | None:
        snapshot = await self._turns.document(_required(key, 'turn_id')).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def submit(
        self, *, uid: str, conversation_id: str, client_message_id: str, text: str
    ) -> tuple[dict[str, Any], bool]:
        """Record the user message and its queued turn once; return `(turn, created)`."""
        _required(text, 'text')
        key = turn_id(uid, conversation_id, client_message_id)
        immutable = {
            'schema_version': SCHEMA_VERSION,
            'turn_id': key,
            'uid': uid,
            'conversation_id': conversation_id,
            'client_message_id': client_message_id,
            'text': text,
        }
        turn_ref = self._turns.document(key)
        conversation_ref = self._conversation(uid, conversation_id)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[dict[str, Any], bool]:
            existing = await turn_ref.get(transaction=transaction)
            if existing.exists:
                record = existing.to_dict() or {}
                if any(record.get(field) != immutable[field] for field in _IMMUTABLE):
                    raise TurnConflict('client message id already belongs to a different message')
                return record, False
            conversation = await conversation_ref.get(transaction=transaction)
            head = (conversation.to_dict() or {}) if conversation.exists else {}
            when = self._now()
            seq = int(head.get('last_seq', 0)) + 1
            record = {
                **immutable,
                'seq': seq,
                'status': QUEUED,
                'attempt': 0,
                'run_ids': [],
                'lease_owner': None,
                'lease_expires_at': None,
                'result': None,
                'error': None,
                'created_at': when,
                'updated_at': when,
                # Set before the first delivery so the repair range query can match it.
                'dispatched_at': when,
            }
            transaction.set(turn_ref, record)
            transaction.set(
                conversation_ref,
                {
                    'conversation_id': conversation_id,
                    # The first message names the conversation until the owner renames it.
                    'title': head.get('title') or text.strip().splitlines()[0][:_TITLE_CHARS],
                    'last_seq': seq,
                    'settled_seq': int(head.get('settled_seq', 0)),
                    'head_run_id': head.get('head_run_id'),
                    'revision': int(head.get('revision', 0)),
                    'created_at': head.get('created_at', when),
                    'updated_at': when,
                },
            )
            transaction.set(
                self._message(record, 'user'),
                {'role': 'user', 'text': text, 'turn_id': key, 'position': 2 * seq, 'created_at': when},
            )
            return record, True

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    async def claim(self, key: str, *, worker_id: str, lease: timedelta) -> Claim | None:
        """Lease the next attempt, or return `None` if the turn is not runnable now.

        `None` covers a settled turn, a live lease held by another attempt, and
        a turn whose earlier conversation turns have not settled yet; the
        dispatcher retries the last two later.
        """
        _required(worker_id, 'worker_id')
        turn_ref = self._turns.document(_required(key, 'turn_id'))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Claim | None:
            snapshot = await turn_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(key)
            turn = snapshot.to_dict() or {}
            conversation_ref = self._conversation(turn['uid'], turn['conversation_id'])
            conversation = (await conversation_ref.get(transaction=transaction)).to_dict() or {}
            now = self._now()
            if turn['status'] in SETTLED:
                return None
            if turn['status'] == RUNNING and turn['lease_expires_at'] > now:
                return None
            if turn.get('cancel_requested_at') is not None and not turn['run_ids']:
                # Nothing ran yet, so nothing can be uncertain. A turn with earlier attempts is
                # claimed normally and its worker checks their tool effects before cancelling.
                self._settle(transaction, turn_ref, turn, conversation_ref, conversation, CANCELLED, now,
                             message=dict(_CANCELLED_NOTICE))
                return None
            if int(conversation.get('settled_seq', 0)) != int(turn['seq']) - 1:
                return None
            attempt = int(turn['attempt']) + 1
            run_id = f'{key}.{attempt}'
            previous = turn['run_ids'][-1] if turn['run_ids'] else None
            updates = {
                'status': RUNNING,
                'attempt': attempt,
                'run_ids': [*turn['run_ids'], run_id],
                'lease_owner': worker_id,
                'lease_expires_at': now + lease,
                'started_at': turn.get('started_at') or now,
                'updated_at': now,
            }
            transaction.update(turn_ref, updates)
            return Claim(
                turn={**turn, **updates},
                attempt=attempt,
                run_id=run_id,
                previous_run_id=previous,
                head_run_id=conversation.get('head_run_id'),
            )

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    def _settle(
        self,
        transaction: AsyncTransaction,
        turn_ref: Any,
        turn: dict[str, Any],
        conversation_ref: Any,
        conversation: dict[str, Any],
        status: str,
        now: datetime,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        message: dict[str, Any] | None = None,
        move_head_to: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write a status change, its conversation effects and its message inside `transaction`.

        Every read must happen before this call: Firestore transactions read before they write.
        """
        updates: dict[str, Any] = {
            **(extra or {}),
            'status': status,
            'lease_owner': None,
            'lease_expires_at': None,
            'result': result,
            'error': error,
            'updated_at': now,
        }
        if status != QUEUED:
            updates['finished_at'] = now
        transaction.update(turn_ref, updates)
        if status != QUEUED:
            seq = int(turn['seq'])
            settled = int(conversation.get('settled_seq', 0))
            head_updates: dict[str, Any] = {**_settle_seq(conversation, seq), 'updated_at': now}
            # A reconciled older turn must not move the head behind later completed turns.
            if move_head_to is not None and settled <= seq:
                head_updates['head_run_id'] = move_head_to
                head_updates['revision'] = int(conversation.get('revision', 0)) + 1
            transaction.update(conversation_ref, head_updates)
        if message is not None:
            activity = turn.get('activity') or []
            transaction.set(
                self._message(turn, message['role']),
                {
                    **message,
                    **({'activity': activity} if activity else {}),
                    'started_at': turn.get('started_at'),
                    'turn_id': turn['turn_id'],
                    'position': 2 * int(turn['seq']) + 1,
                    'created_at': now,
                },
            )
        return {**turn, **updates}

    async def _finish(
        self,
        key: str,
        run_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        message: dict[str, Any] | None = None,
        move_head_to: str | None = None,
    ) -> dict[str, Any]:
        turn_ref = self._turns.document(_required(key, 'turn_id'))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> dict[str, Any]:
            snapshot = await turn_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(key)
            turn = snapshot.to_dict() or {}
            current = turn['status']
            if current == status and turn['run_ids'] and turn['run_ids'][-1] == run_id:
                if (turn.get('result'), turn.get('error')) != (result, error):
                    raise TurnConflict(f'turn already {status} with a different outcome')
                return turn
            if status not in _ALLOWED.get(current, ()):
                raise InvalidTransition(f'cannot change turn from {current} to {status}')
            if not turn['run_ids'] or turn['run_ids'][-1] != run_id:
                raise InvalidTransition(f'run {run_id!r} is not the current attempt of this turn')
            conversation_ref = self._conversation(turn['uid'], turn['conversation_id'])
            conversation = (await conversation_ref.get(transaction=transaction)).to_dict() or {}
            return self._settle(
                transaction, turn_ref, turn, conversation_ref, conversation, status, self._now(),
                result=result, error=error, message=message, move_head_to=move_head_to,
            )

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    async def record_activity(self, key: str, run_id: str, activity: list[dict[str, Any]]) -> bool:
        """Replace the tool activity shown for a running turn; a stale attempt changes nothing."""
        turn_ref = self._turns.document(_required(key, 'turn_id'))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> bool:
            snapshot = await turn_ref.get(transaction=transaction)
            turn = snapshot.to_dict() or {}
            if turn.get('status') != RUNNING or not turn.get('run_ids') or turn['run_ids'][-1] != run_id:
                return False
            transaction.update(turn_ref, {'activity': activity, 'updated_at': self._now()})
            return True

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    async def request_cancel(self, key: str, *, uid: str) -> dict[str, Any]:
        """Cancel the owner's turn: at once when queued, through its worker when running.

        Cancelling an already cancelled turn returns it unchanged; any other
        settled turn raises `InvalidTransition`. A turn of another user is
        reported as missing.
        """
        turn_ref = self._turns.document(_required(key, 'turn_id'))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> dict[str, Any]:
            snapshot = await turn_ref.get(transaction=transaction)
            turn = snapshot.to_dict() if snapshot.exists else None
            if turn is None or turn['uid'] != uid:
                raise KeyError(key)
            status = turn['status']
            if status == CANCELLED:
                return turn
            if status in SETTLED:
                raise InvalidTransition(f'turn is already {status}')
            now = self._now()
            requested = {'cancel_requested_at': turn.get('cancel_requested_at') or now}
            if status == QUEUED and not turn['run_ids']:
                conversation_ref = self._conversation(turn['uid'], turn['conversation_id'])
                conversation = (await conversation_ref.get(transaction=transaction)).to_dict() or {}
                return self._settle(transaction, turn_ref, {**turn, **requested}, conversation_ref, conversation,
                                    CANCELLED, now, message=dict(_CANCELLED_NOTICE), extra=requested)
            if turn.get('cancel_requested_at') is None:
                transaction.update(turn_ref, {**requested, 'updated_at': now})
            return {**turn, **requested}

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    async def cancel_requested(self, key: str) -> bool:
        turn = await self.get(key)
        return turn is not None and turn.get('cancel_requested_at') is not None

    async def cancel(self, key: str, run_id: str) -> dict[str, Any]:
        """Settle the current attempt as cancelled by the owner; nothing is left uncertain."""
        return await self._finish(key, run_id, CANCELLED, message=dict(_CANCELLED_NOTICE))

    async def update_conversation(
        self, uid: str, conversation_id: str, *, title: str | None = None, pinned: bool | None = None
    ) -> dict[str, Any]:
        """Rename or pin an existing conversation; raise `KeyError` when it does not exist."""
        conversation_ref = self._conversation(uid, conversation_id)
        updates: dict[str, Any] = {}
        if title is not None:
            if not title.strip():
                raise ValueError('title must not be empty')
            updates['title'] = title.strip()[:_TITLE_CHARS * 2]
        if pinned is not None:
            updates['pinned'] = pinned
        snapshot = await conversation_ref.get()
        if not snapshot.exists:
            raise KeyError(conversation_id)
        if updates:
            await conversation_ref.update(updates)
        return {**(snapshot.to_dict() or {}), **updates}

    async def delete_conversation(self, uid: str, conversation_id: str) -> list[str]:
        """Delete a settled conversation, its messages and its turns; return the run ids it used.

        The caller deletes those runs from the step store, which holds the
        model-facing copy of the conversation. A conversation with a queued or
        running turn raises `ConversationBusy`.
        """
        conversation_ref = self._conversation(uid, conversation_id)
        if not (await conversation_ref.get()).exists:
            raise KeyError(conversation_id)
        turns = [
            snapshot
            async for snapshot in self._turns.where(filter=FieldFilter('uid', '==', uid))
            .where(filter=FieldFilter('conversation_id', '==', conversation_id))
            .stream()
        ]
        if any((snapshot.to_dict() or {}).get('status') not in SETTLED for snapshot in turns):
            raise ConversationBusy(conversation_id)
        run_ids = sorted({run for snapshot in turns for run in (snapshot.to_dict() or {}).get('run_ids', [])})
        references = [snapshot.reference async for snapshot in conversation_ref.collection('messages').stream()]
        references += [snapshot.reference for snapshot in turns]
        for start in range(0, len(references), _BATCH_OPS):
            batch = self._client.batch()
            for reference in references[start:start + _BATCH_OPS]:
                batch.delete(reference)
            await batch.commit()
        await conversation_ref.delete()
        return run_ids

    async def complete(
        self,
        key: str,
        run_id: str,
        *,
        reply: str,
        history_run_id: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Record the reply, append it to the conversation and move the head.

        The head moves to `history_run_id` when an earlier attempt already
        produced the reply and the current attempt only recorded it; otherwise
        to `run_id`. `usage` holds the token and request counts the provider
        reported for this attempt, when it reported any.
        """
        history = history_run_id or run_id
        result: dict[str, Any] = {'reply': reply, 'history_run_id': history}
        if usage:
            result['usage'] = usage
        return await self._finish(
            key, run_id, COMPLETED, result=result,
            message={'role': 'assistant', 'text': reply, **({'usage': usage} if usage else {})},
            move_head_to=history,
        )

    async def close_reconciliation(self, key: str, *, uid: str) -> dict[str, Any]:
        """The owner checked an uncertain turn and closes it; later turns are not affected."""
        turn = await self.get(key)
        if turn is None or turn['uid'] != uid:
            raise KeyError(key)
        if turn['status'] != RECONCILING:
            raise InvalidTransition(f'turn is {turn["status"]}, not awaiting reconciliation')
        return await self._finish(
            key, turn['run_ids'][-1], CANCELLED, message={'role': 'notice', 'type': 'closed_by_owner'}
        )

    async def fail(self, key: str, run_id: str, *, error_type: str, detail: str) -> dict[str, Any]:
        """Settle the turn as failed without moving the head; the user sees the failure."""
        error = {'type': error_type, 'detail': detail}
        return await self._finish(key, run_id, FAILED, error=error, message={'role': 'error', **error})

    async def require_reconciliation(self, key: str, run_id: str, *, unresolved: list[str]) -> dict[str, Any]:
        """Stop automatic attempts: a tool may or may not have acted. Later turns may proceed."""
        error = {'type': 'unresolved_tool_effects', 'detail': ', '.join(unresolved)}
        return await self._finish(key, run_id, RECONCILING, error=error, message={'role': 'error', **error})

    async def release(self, key: str, run_id: str, *, error_type: str, detail: str) -> dict[str, Any]:
        """Return a failed attempt to the queue for a retry that continues from its facts."""
        return await self._finish(key, run_id, QUEUED, error={'type': error_type, 'detail': detail})
