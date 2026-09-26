"""Worker recovery decisions on real stores (Firestore emulator), without a model call.

Each test leaves in the step store what a crashed attempt leaves behind,
using Harness record types, and checks the next attempt's decision. Paths
that call a model are covered by the live turn test with a real model.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from google.cloud.firestore import AsyncClient
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord, StepEvent, ToolEffectRecord

from jarvis_core.firestore_stores import FirestoreMemoryStore, FirestoreStepStore
from jarvis_core.secrets import SecretResolver
from jarvis_core.settings import FirestoreSettingsStore
from jarvis_core.turns import COMPLETED, FAILED, RECONCILING, FirestoreTurnStore
from jarvis_core.worker import TurnWorker, final_reply, provider_reasons

pytestmark = pytest.mark.anyio


@pytest.fixture
def worker(firestore_client: AsyncClient) -> TurnWorker:
    return TurnWorker(
        turns=FirestoreTurnStore(firestore_client),
        settings=FirestoreSettingsStore(firestore_client),
        steps=FirestoreStepStore(firestore_client, media_store=None),
        memory=FirestoreMemoryStore(firestore_client),
        secrets=SecretResolver(),
        worker_id='test-worker',
    )


async def _crashed_attempt(worker: TurnWorker) -> tuple[str, str]:
    """Submit a turn and leave its first attempt abandoned; return (turn_id, run_id)."""
    record, _ = await worker.turns.submit(uid='u1', conversation_id='c1', client_message_id='m1', text='send it')
    claim = await worker.turns.claim(record['turn_id'], worker_id='crashed', lease=timedelta(minutes=5))
    assert claim is not None
    await worker.turns.release(record['turn_id'], claim.run_id, error_type='WorkerLost', detail='test')
    await worker.steps.register_run(RunRecord(run_id=claim.run_id, conversation_id='c1'))
    return record['turn_id'], claim.run_id


_REQUEST = ModelRequest(parts=[UserPromptPart(content='send it')])
_CALL = ModelResponse(parts=[ToolCallPart(tool_name='send_email', args={}, tool_call_id='call-1')])


async def test_started_effect_without_outcome_requires_reconciliation(worker: TurnWorker) -> None:
    key, previous = await _crashed_attempt(worker)
    await worker.steps.record_tool_effect(
        ToolEffectRecord(tool_call_id='call-1', tool_name='send_email', run_id=previous, status='started')
    )
    assert await worker.run(key) == 'reconciling'
    turn = await worker.turns.get(key)
    assert turn is not None and turn['status'] == RECONCILING
    assert turn['error'] == {'type': 'unresolved_tool_effects', 'detail': 'send_email:call-1'}


async def test_effect_completed_after_last_checkpoint_requires_reconciliation(worker: TurnWorker) -> None:
    key, previous = await _crashed_attempt(worker)
    await worker.steps.save_snapshot(ContinuableSnapshot(run_id=previous, step_index=1, messages=[_REQUEST, _CALL]))
    await worker.steps.record_tool_effect(
        ToolEffectRecord(tool_call_id='call-1', tool_name='send_email', run_id=previous, status='completed')
    )
    await worker.steps.append_event(
        StepEvent(run_id=previous, kind='tool_call_completed', step_index=2, tool_call_id='call-1', tool_name='send_email')
    )
    assert await worker.run(key) == 'reconciling'


async def test_checkpointed_effect_is_not_uncertain(worker: TurnWorker) -> None:
    key, previous = await _crashed_attempt(worker)
    returned = ModelRequest(parts=[ToolReturnPart(tool_name='send_email', content='sent', tool_call_id='call-1')])
    done = ModelResponse(parts=[TextPart(content='Sent.')])
    await worker.steps.save_snapshot(
        ContinuableSnapshot(run_id=previous, step_index=3, messages=[_REQUEST, _CALL, returned, done])
    )
    await worker.steps.record_tool_effect(
        ToolEffectRecord(tool_call_id='call-1', tool_name='send_email', run_id=previous, status='completed')
    )
    await worker.steps.append_event(
        StepEvent(run_id=previous, kind='tool_call_completed', step_index=2, tool_call_id='call-1', tool_name='send_email')
    )
    # The previous attempt reached its final reply, so it is recorded without a model call.
    assert await worker.run(key) == 'completed'
    turn = await worker.turns.get(key)
    assert turn is not None and turn['status'] == COMPLETED
    assert turn['result'] == {'reply': 'Sent.', 'history_run_id': previous}
    conversation = await worker.turns._conversation('u1', 'c1').get()
    assert conversation.to_dict()['head_run_id'] == previous


async def test_attempts_are_bounded(worker: TurnWorker) -> None:
    worker.max_attempts = 1
    key, _ = await _crashed_attempt(worker)
    assert await worker.run(key) == 'failed'
    turn = await worker.turns.get(key)
    assert turn is not None and turn['status'] == FAILED and turn['error']['type'] == 'AttemptsExhausted'


async def test_first_attempt_without_settings_fails_visibly(worker: TurnWorker) -> None:
    record, _ = await worker.turns.submit(uid='u1', conversation_id='c1', client_message_id='m1', text='hi')
    assert await worker.run(record['turn_id']) == 'failed'
    turn = await worker.turns.get(record['turn_id'])
    assert turn is not None and turn['error']['type'] == 'SettingsMissing'


async def test_not_runnable_when_earlier_turn_is_open(worker: TurnWorker) -> None:
    await worker.turns.submit(uid='u1', conversation_id='c1', client_message_id='m1', text='first')
    second, _ = await worker.turns.submit(uid='u1', conversation_id='c1', client_message_id='m2', text='second')
    assert await worker.run(second['turn_id']) == 'not_runnable'


def test_provider_reasons_keep_codes_and_drop_messages_and_links() -> None:
    # Shape of the Cloud Code 403 observed through CLIProxyAPI on 2026-09-26.
    body = {
        'error': {
            'code': 403,
            'message': 'Verify your account to continue.',
            'status': 'PERMISSION_DENIED',
            'details': [
                {
                    '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
                    'reason': 'VALIDATION_REQUIRED',
                    'metadata': {'validation_url': 'https://accounts.google.com/signin/continue?plt=opaque'},
                },
                {'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'url': 'https://example.invalid'}]},
            ],
        }
    }
    assert provider_reasons(body) == ['PERMISSION_DENIED', 'VALIDATION_REQUIRED']
    assert provider_reasons(json.dumps(body)) == ['PERMISSION_DENIED', 'VALIDATION_REQUIRED']
    assert provider_reasons([{'error': {'status': 'RESOURCE_EXHAUSTED'}}]) == ['RESOURCE_EXHAUSTED']
    assert provider_reasons({'error': {'status': 'not a code https://x'}}) == []
    assert provider_reasons('plain text body') == []
    assert provider_reasons(None) == []


def test_final_reply_requires_a_text_response_without_tool_calls() -> None:
    assert final_reply([]) is None
    assert final_reply([_REQUEST]) is None
    assert final_reply([_REQUEST, _CALL]) is None
    assert final_reply([_REQUEST, ModelResponse(parts=[TextPart(content='a'), TextPart(content='b')])]) == 'ab'
