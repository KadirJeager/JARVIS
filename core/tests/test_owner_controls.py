"""Owner controls on the Firestore emulator: cancel, activity, conversation edits and deletion, vault listing.

Also covers the pure helpers behind notifications and the capability manifest.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.cloud.firestore import AsyncClient
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord, StepEvent, ToolEffectRecord

from jarvis_core.assistant import build_agent, capability_manifest
from jarvis_core.firestore_stores import FirestoreMemoryStore, FirestoreStepStore
from jarvis_core.notify import PushSubscriptionIn, _load_key, _public_key, snippet, subscription_id
from jarvis_core.secrets import SecretResolver
from jarvis_core.settings import AssistantSettings, FirestoreSettingsStore, ModelConnection
from jarvis_core.turns import (
    CANCELLED,
    COMPLETED,
    RECONCILING,
    RUNNING,
    ConversationBusy,
    FirestoreTurnStore,
    InvalidTransition,
)
from jarvis_core.worker import TurnWorker, _Cancelled, usage_record

pytestmark = pytest.mark.anyio

LEASE = timedelta(minutes=5)


@pytest.fixture
def turns(firestore_client: AsyncClient) -> FirestoreTurnStore:
    return FirestoreTurnStore(firestore_client)


@pytest.fixture
def worker(firestore_client: AsyncClient, turns: FirestoreTurnStore) -> TurnWorker:
    return TurnWorker(
        turns=turns,
        settings=FirestoreSettingsStore(firestore_client),
        steps=FirestoreStepStore(firestore_client, media_store=None),
        memory=FirestoreMemoryStore(firestore_client),
        secrets=SecretResolver(),
        worker_id='test-worker',
        cancel_poll_seconds=0.05,
    )


async def _submit(turns: FirestoreTurnStore, message_id: str, text: str = 'hello', conversation: str = 'c1') -> str:
    record, _ = await turns.submit(uid='u1', conversation_id=conversation, client_message_id=message_id, text=text)
    return record['turn_id']


async def _messages(client: AsyncClient, conversation: str = 'c1') -> list[dict]:
    query = (
        client.collection('users').document('u1').collection('conversations').document(conversation)
        .collection('messages').order_by('position')
    )
    return [doc.to_dict() async for doc in query.stream()]


async def _conversation(client: AsyncClient, conversation: str = 'c1') -> dict | None:
    doc = await client.collection('users').document('u1').collection('conversations').document(conversation).get()
    return doc.to_dict() if doc.exists else None


# -- cancel ------------------------------------------------------------------


async def test_cancel_of_a_queued_turn_settles_it_at_once(turns: FirestoreTurnStore, firestore_client: AsyncClient):
    first = await _submit(turns, 'm1', 'first')
    second = await _submit(turns, 'm2', 'second')
    cancelled = await turns.request_cancel(first, uid='u1')
    assert cancelled['status'] == CANCELLED and cancelled['finished_at'] is not None
    assert (await _conversation(firestore_client))['settled_seq'] == 1
    assert [(m['role'], m.get('type', m.get('text'))) for m in await _messages(firestore_client)] == [
        ('user', 'first'), ('notice', 'cancelled'), ('user', 'second'),
    ]
    assert await turns.claim(second, worker_id='w', lease=LEASE) is not None
    # Repeating the request is harmless.
    assert (await turns.request_cancel(first, uid='u1'))['status'] == CANCELLED


async def test_cancelled_turn_behind_an_open_one_never_lets_later_turns_jump_ahead(
    turns: FirestoreTurnStore, firestore_client: AsyncClient
):
    first = await _submit(turns, 'm1', 'first')
    second = await _submit(turns, 'm2', 'second')
    third = await _submit(turns, 'm3', 'third')
    claim = await turns.claim(first, worker_id='w', lease=LEASE)
    assert claim is not None
    await turns.request_cancel(second, uid='u1')
    conversation = await _conversation(firestore_client)
    assert conversation['settled_seq'] == 0 and conversation['settled_ahead'] == [2]
    assert await turns.claim(third, worker_id='w', lease=LEASE) is None

    await turns.complete(first, claim.run_id, reply='one')
    conversation = await _conversation(firestore_client)
    assert conversation['settled_seq'] == 2 and conversation['settled_ahead'] == []
    assert await turns.claim(third, worker_id='w', lease=LEASE) is not None


async def test_cancel_of_a_running_turn_is_a_request_for_its_worker(turns: FirestoreTurnStore):
    key = await _submit(turns, 'm1')
    claim = await turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    requested = await turns.request_cancel(key, uid='u1')
    assert requested['status'] == RUNNING and requested['cancel_requested_at'] is not None
    assert await turns.cancel_requested(key)
    done = await turns.cancel(key, claim.run_id)
    assert done['status'] == CANCELLED


async def test_cancel_is_refused_for_settled_and_foreign_turns(turns: FirestoreTurnStore):
    key = await _submit(turns, 'm1')
    claim = await turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await turns.complete(key, claim.run_id, reply='done')
    with pytest.raises(InvalidTransition):
        await turns.request_cancel(key, uid='u1')
    with pytest.raises(KeyError):
        await turns.request_cancel(key, uid='someone-else')


async def test_worker_stops_a_running_attempt_when_cancel_is_requested(worker: TurnWorker):
    key = await _submit(worker.turns, 'm1')
    claim = await worker.turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await worker.turns.request_cancel(key, uid='u1')
    started = asyncio.get_running_loop().time()
    with pytest.raises(_Cancelled):
        await worker._until_cancelled(key, asyncio.sleep(30))
    assert asyncio.get_running_loop().time() - started < 5
    assert await worker._settle_cancelled(claim, claim.run_id) == 'cancelled'
    turn = await worker.turns.get(key)
    assert turn is not None and turn['status'] == CANCELLED


async def test_cancel_after_a_cut_off_tool_call_waits_for_reconciliation(worker: TurnWorker):
    key = await _submit(worker.turns, 'm1')
    claim = await worker.turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await worker.steps.register_run(RunRecord(run_id=claim.run_id, conversation_id='c1'))
    await worker.steps.record_tool_effect(
        ToolEffectRecord(tool_call_id='call-1', tool_name='send_email', run_id=claim.run_id, status='started')
    )
    assert await worker._settle_cancelled(claim, claim.run_id) == 'reconciling'
    turn = await worker.turns.get(key)
    assert turn is not None and turn['status'] == RECONCILING
    closed = await worker.turns.close_reconciliation(key, uid='u1')
    assert closed['status'] == CANCELLED


async def test_a_released_turn_cancelled_meanwhile_is_cancelled_by_its_next_claim(worker: TurnWorker):
    key = await _submit(worker.turns, 'm1')
    claim = await worker.turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await worker.turns.request_cancel(key, uid='u1')
    await worker.turns.release(key, claim.run_id, error_type='ModelHTTPError', detail='HTTP 503')
    assert await worker.run(key) == 'cancelled'
    turn = await worker.turns.get(key)
    assert turn is not None and turn['status'] == CANCELLED


# -- activity ----------------------------------------------------------------


async def test_activity_is_recorded_for_the_current_attempt_and_kept_on_the_reply(
    turns: FirestoreTurnStore, firestore_client: AsyncClient
):
    key = await _submit(turns, 'm1')
    claim = await turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    items = [{'id': 'call-1', 'tool': 'web_fetch', 'args': '{"url": "https://example.org"}', 'state': 'done'}]
    assert await turns.record_activity(key, claim.run_id, items)
    assert not await turns.record_activity(key, f'{key}.99', [])
    await turns.complete(key, claim.run_id, reply='read it', usage={'requests': 2, 'output_tokens': 40})
    turn = await turns.get(key)
    assert turn is not None and turn['result']['usage'] == {'requests': 2, 'output_tokens': 40}
    reply = (await _messages(firestore_client))[-1]
    assert reply['role'] == 'assistant' and reply['activity'] == items
    assert not await turns.record_activity(key, claim.run_id, [])


def test_usage_record_keeps_only_reported_counts():
    assert usage_record(RunUsage(requests=2, input_tokens=100, output_tokens=0)) == {
        'requests': 2, 'input_tokens': 100,
    }


# -- conversations -----------------------------------------------------------


async def test_rename_and_pin(turns: FirestoreTurnStore, firestore_client: AsyncClient):
    await _submit(turns, 'm1', 'first message')
    await turns.update_conversation('u1', 'c1', title='  Renamed  ', pinned=True)
    conversation = await _conversation(firestore_client)
    assert conversation['title'] == 'Renamed' and conversation['pinned'] is True
    with pytest.raises(KeyError):
        await turns.update_conversation('u1', 'missing', title='x')


async def test_delete_conversation_removes_messages_turns_and_step_runs(
    turns: FirestoreTurnStore, firestore_client: AsyncClient
):
    steps = FirestoreStepStore(firestore_client, media_store=None)
    key = await _submit(turns, 'm1')
    other = await _submit(turns, 'm9', conversation='c2')
    with pytest.raises(ConversationBusy):
        await turns.delete_conversation('u1', 'c1')
    claim = await turns.claim(key, worker_id='w', lease=LEASE)
    assert claim is not None
    await steps.register_run(RunRecord(run_id=claim.run_id, conversation_id='c1'))
    await steps.append_event(StepEvent(run_id=claim.run_id, kind='run_started', step_index=0))
    request = ModelRequest(parts=[UserPromptPart(content='hello')])
    await steps.save_snapshot(ContinuableSnapshot(run_id=claim.run_id, step_index=1, messages=[request]))
    await steps.record_tool_effect(
        ToolEffectRecord(tool_call_id='call-1', tool_name='web_fetch', run_id=claim.run_id, status='completed')
    )
    await turns.complete(key, claim.run_id, reply='hi')

    run_ids = await turns.delete_conversation('u1', 'c1')
    assert run_ids == [claim.run_id]
    for run_id in run_ids:
        await steps.delete_run(run_id=run_id)

    assert await _conversation(firestore_client) is None
    assert await _messages(firestore_client) == []
    assert await turns.get(key) is None
    assert await turns.get(other) is not None
    assert await steps.get_run(run_id=claim.run_id) is None
    assert await steps.list_events(run_id=claim.run_id) == []
    assert await steps.latest_snapshot(run_id=claim.run_id) is None
    assert await steps.get_tool_effect(run_id=claim.run_id, tool_call_id='call-1') is None
    with pytest.raises(KeyError):
        await turns.delete_conversation('u1', 'c1')


# -- vault listing -----------------------------------------------------------


async def test_vault_listing_reports_size_and_change_time(firestore_client: AsyncClient):
    memory = FirestoreMemoryStore(firestore_client)
    await memory.write('u1/jarvis/MEMORY.md', 'line one\nline two', expected_version=None)
    await memory.write('u1/jarvis/profil.md', 'x' * 10, expected_version=None)
    await memory.write('u2/jarvis/MEMORY.md', 'other user', expected_version=None)
    files = await memory.list_files('u1/jarvis/', limit=10)
    assert [(file['path'], file['chars']) for file in files] == [('u1/jarvis/MEMORY.md', 17), ('u1/jarvis/profil.md', 10)]
    assert all(file['updated_at'] is not None and file['version'] for file in files)
    assert await memory.read_all('u1/jarvis/', limit=10) == [
        ('u1/jarvis/MEMORY.md', 'line one\nline two'), ('u1/jarvis/profil.md', 'x' * 10),
    ]


# -- notifications -----------------------------------------------------------


def test_snippet_is_plain_text():
    assert snippet('# Başlık\n\n**Kalın** ve `kod` ile [bağlantı](https://example.org).') == (
        'Başlık Kalın ve kod ile bağlantı.'
    )
    assert len(snippet('a' * 1000)) == 280


def test_subscription_endpoint_must_be_a_public_https_host():
    keys = {'p256dh': 'k', 'auth': 'a'}
    assert PushSubscriptionIn(endpoint='https://fcm.googleapis.com/fcm/send/x', keys=keys).label == ''
    for endpoint in ('http://fcm.googleapis.com/x', 'https://127.0.0.1/x', 'https://localhost/x', 'https://[::1]/x'):
        with pytest.raises(ValidationError):
            PushSubscriptionIn(endpoint=endpoint, keys=keys)
    assert subscription_id('https://a/1') != subscription_id('https://a/2')


def test_vapid_key_round_trip():
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = _public_key(_load_key(pem))
    assert len(public) == 87 and '=' not in public
    other = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with pytest.raises(ValueError):
        _load_key(other)


# -- capability manifest -----------------------------------------------------


async def test_capability_manifest_matches_the_built_agent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('JARVIS_TEST_KEY', 'not-used')
    settings = AssistantSettings(model=ModelConnection(protocol='google', model='m', api_key_ref='env:JARVIS_TEST_KEY'))
    agent = await build_agent(
        settings, uid='u1', run_id='r1', memory_store=None,  # type: ignore[arg-type]
        step_store=FirestoreStepStore.__new__(FirestoreStepStore), secrets=SecretResolver(),
    )
    built = {
        capability.id: 'deferred' if capability.defer_loading else 'core'
        for capability in agent.root_capability.capabilities
        if getattr(capability, 'id', None)
    }
    assert built == {item['id']: item['loading'] for item in capability_manifest(settings)}
