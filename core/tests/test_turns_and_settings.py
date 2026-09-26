"""Turn ledger and settings store behavior on the Firestore emulator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud.firestore import AsyncClient

from jarvis_core.settings import AssistantSettings, FirestoreSettingsStore, ModelConnection, SettingsConflictError
from jarvis_core.turns import (
    COMPLETED,
    FAILED,
    QUEUED,
    RECONCILING,
    FirestoreTurnStore,
    InvalidTransition,
    TurnConflict,
)

pytestmark = pytest.mark.anyio

LEASE = timedelta(minutes=5)


class Clock:
    """Time source the test advances explicitly to cross lease expiry."""

    def __init__(self) -> None:
        self.value = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def turns(firestore_client: AsyncClient, clock: Clock) -> FirestoreTurnStore:
    return FirestoreTurnStore(firestore_client, now=clock)


async def _submit(turns: FirestoreTurnStore, message_id: str, text: str = 'hello') -> str:
    record, _ = await turns.submit(uid='u1', conversation_id='c1', client_message_id=message_id, text=text)
    return record['turn_id']


async def _messages(client: AsyncClient) -> list[tuple[str, str]]:
    query = client.collection('users').document('u1').collection('conversations').document('c1').collection('messages')
    docs = [doc.to_dict() async for doc in query.order_by('position').stream()]
    return [(doc['role'], doc['text'] if 'text' in doc else doc['type']) for doc in docs]


async def _conversation(client: AsyncClient) -> dict:
    doc = await client.collection('users').document('u1').collection('conversations').document('c1').get()
    return doc.to_dict()


async def test_submit_is_idempotent_and_rejects_changed_content(turns: FirestoreTurnStore) -> None:
    first, created = await turns.submit(uid='u1', conversation_id='c1', client_message_id='m1', text='hello')
    again, created_again = await turns.submit(uid='u1', conversation_id='c1', client_message_id='m1', text='hello')
    assert created and not created_again
    assert again['turn_id'] == first['turn_id'] and again['seq'] == 1 and again['status'] == QUEUED
    with pytest.raises(TurnConflict):
        await turns.submit(uid='u1', conversation_id='c1', client_message_id='m1', text='changed')


async def test_turns_run_in_submit_order_one_at_a_time(
    turns: FirestoreTurnStore, firestore_client: AsyncClient
) -> None:
    first = await _submit(turns, 'm1', 'first')
    second = await _submit(turns, 'm2', 'second')

    assert await turns.claim(second, worker_id='w', lease=LEASE) is None
    claim = await turns.claim(first, worker_id='w', lease=LEASE)
    assert claim is not None and claim.attempt == 1 and claim.previous_run_id is None and claim.head_run_id is None
    assert await turns.claim(second, worker_id='w', lease=LEASE) is None

    await turns.complete(first, claim.run_id, reply='reply one')
    second_claim = await turns.claim(second, worker_id='w', lease=LEASE)
    assert second_claim is not None and second_claim.head_run_id == claim.run_id
    await turns.complete(second, second_claim.run_id, reply='reply two')

    assert await _messages(firestore_client) == [
        ('user', 'first'), ('assistant', 'reply one'), ('user', 'second'), ('assistant', 'reply two')
    ]
    head = await _conversation(firestore_client)
    assert head['head_run_id'] == second_claim.run_id and head['revision'] == 2 and head['settled_seq'] == 2
    assert head['title'] == 'first'  # named by the first message, not renamed by later ones


async def test_expired_lease_allows_a_new_attempt_and_fences_the_old_one(
    turns: FirestoreTurnStore, clock: Clock
) -> None:
    key = await _submit(turns, 'm1')
    first = await turns.claim(key, worker_id='w1', lease=LEASE)
    assert first is not None
    clock.value += LEASE - timedelta(seconds=1)
    assert await turns.claim(key, worker_id='w2', lease=LEASE) is None

    clock.value += timedelta(seconds=2)
    second = await turns.claim(key, worker_id='w2', lease=LEASE)
    assert second is not None and second.attempt == 2 and second.previous_run_id == first.run_id
    with pytest.raises(InvalidTransition):
        await turns.complete(key, first.run_id, reply='stale worker')
    done = await turns.complete(key, second.run_id, reply='fresh worker')
    assert done['status'] == COMPLETED and done['run_ids'] == [first.run_id, second.run_id]


async def test_release_requeues_and_the_retry_sees_the_previous_run(turns: FirestoreTurnStore) -> None:
    key = await _submit(turns, 'm1')
    first = await turns.claim(key, worker_id='w', lease=LEASE)
    assert first is not None
    released = await turns.release(key, first.run_id, error_type='ModelHTTPError', detail='503')
    assert released['status'] == QUEUED and released['lease_owner'] is None
    retry = await turns.claim(key, worker_id='w', lease=LEASE)
    assert retry is not None and retry.attempt == 2 and retry.previous_run_id == first.run_id


async def test_failure_settles_without_moving_head(turns: FirestoreTurnStore, firestore_client: AsyncClient) -> None:
    first = await _submit(turns, 'm1', 'first')
    second = await _submit(turns, 'm2', 'second')
    claim = await turns.claim(first, worker_id='w', lease=LEASE)
    assert claim is not None
    failed = await turns.fail(first, claim.run_id, error_type='UsageLimitExceeded', detail='request_limit')
    assert failed['status'] == FAILED
    head = await _conversation(firestore_client)
    assert head['head_run_id'] is None and head['settled_seq'] == 1
    next_claim = await turns.claim(second, worker_id='w', lease=LEASE)
    assert next_claim is not None and next_claim.head_run_id is None
    assert await _messages(firestore_client) == [('user', 'first'), ('error', 'UsageLimitExceeded'), ('user', 'second')]


async def test_reconciliation_releases_later_turns_and_never_moves_head_backwards(
    turns: FirestoreTurnStore, firestore_client: AsyncClient
) -> None:
    first = await _submit(turns, 'm1', 'first')
    second = await _submit(turns, 'm2', 'second')
    claim = await turns.claim(first, worker_id='w', lease=LEASE)
    assert claim is not None
    held = await turns.require_reconciliation(first, claim.run_id, unresolved=['send_email:call-7'])
    assert held['status'] == RECONCILING
    assert await turns.claim(first, worker_id='w', lease=LEASE) is None

    second_claim = await turns.claim(second, worker_id='w', lease=LEASE)
    assert second_claim is not None
    await turns.complete(second, second_claim.run_id, reply='reply two')
    await turns.complete(first, claim.run_id, reply='resolved later')
    head = await _conversation(firestore_client)
    assert head['head_run_id'] == second_claim.run_id


async def test_reconciled_turn_moves_head_when_nothing_settled_after_it(
    turns: FirestoreTurnStore, firestore_client: AsyncClient
) -> None:
    key = await _submit(turns, 'm1')
    claim = await turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await turns.require_reconciliation(key, claim.run_id, unresolved=['send_email:call-7'])
    await turns.complete(key, claim.run_id, reply='verified sent')
    assert (await _conversation(firestore_client))['head_run_id'] == claim.run_id


async def test_repeated_completion_is_idempotent_but_a_different_reply_conflicts(turns: FirestoreTurnStore) -> None:
    key = await _submit(turns, 'm1')
    claim = await turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await turns.complete(key, claim.run_id, reply='same')
    assert (await turns.complete(key, claim.run_id, reply='same'))['status'] == COMPLETED
    with pytest.raises(TurnConflict):
        await turns.complete(key, claim.run_id, reply='different')


async def test_concurrent_claims_lease_one_attempt(turns: FirestoreTurnStore) -> None:
    key = await _submit(turns, 'm1')
    claims = await asyncio.gather(*(turns.claim(key, worker_id=f'w{i}', lease=LEASE) for i in range(4)))
    won = [claim for claim in claims if claim is not None]
    assert len(won) == 1 and won[0].attempt == 1


async def test_stale_finds_undelivered_queued_and_expired_leases(turns: FirestoreTurnStore, clock: Clock) -> None:
    first = await _submit(turns, 'm1', 'first')
    second = await _submit(turns, 'm2', 'second')
    await turns.mark_dispatched(second)
    running = await turns.claim(first, worker_id='w', lease=LEASE)
    assert running is not None

    cutoff = clock.value + timedelta(seconds=1)
    clock.value += timedelta(minutes=1)
    await turns.mark_dispatched(second)  # redelivered after the cutoff: not stale
    assert await turns.stale(dispatched_before=cutoff) == []

    clock.value += LEASE
    stale = await turns.stale(dispatched_before=clock.value)
    assert sorted(turn['turn_id'] for turn in stale) == sorted([first, second])


async def test_settings_versions_compare_and_set_with_history(firestore_client: AsyncClient) -> None:
    store = FirestoreSettingsStore(firestore_client)
    assert await store.get('u1') is None
    first = AssistantSettings(model=ModelConnection(protocol='openai', base_url='http://127.0.0.1:1', model='m-a'))
    second = AssistantSettings(model=ModelConnection(protocol='google', model='m-b', api_key_ref='env:K'))

    v1 = await store.save('u1', first, expected_version=None)
    v2 = await store.save('u1', second, expected_version=v1.version)
    assert (v1.version, v2.version) == (1, 2)
    with pytest.raises(SettingsConflictError):
        await store.save('u1', first, expected_version=v1.version)
    current = await store.get('u1')
    assert current is not None and current.settings == second
    old = await store.get_version('u1', 1)
    assert old is not None and old.settings == first
