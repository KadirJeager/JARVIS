"""StepStore contract tests for `FirestoreStepStore` on the Firestore emulator.

Protocol, listing, retention and media tests are ported from
pydantic-ai-harness v0.35.0 `tests/step_persistence/test_mongo.py` (MIT
License, Copyright (c) Pydantic Services Inc.) with `MongoStepStore` replaced
by the Firestore store and `MongoMediaStore` by `DiskMediaStore`. Two Mongo
tests are not ported: the suppression-ledger repair (the Firestore store
writes the key and the snapshot in one transaction, so they cannot diverge)
and the agent round trip on a test model (agent runs are verified with a
real model in the service tests). The last tests cover Firestore-specific
chunking, size limits and concurrent appends.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.cloud.firestore import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from jarvis_core.firestore_stores import FirestoreStepStore
from pydantic_ai_harness.conversation_search import SnapshotHistorySource
from pydantic_ai_harness.media import DiskMediaStore
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    StepStore,
    ToolEffectRecord,
)

pytestmark = pytest.mark.anyio


def _msgs(text: str = 'a') -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content=text)])]


@pytest.fixture
def store(firestore_client: AsyncClient) -> FirestoreStepStore:
    return FirestoreStepStore(firestore_client, media_store=None)


def _bounded(client: AsyncClient, keep: int) -> FirestoreStepStore:
    return FirestoreStepStore(client, media_store=None, max_snapshots_per_run=keep)


def test_implements_step_store_protocol(store: FirestoreStepStore) -> None:
    assert isinstance(store, StepStore)


def test_rejects_invalid_max_snapshots_per_run(firestore_client: AsyncClient) -> None:
    with pytest.raises(ValueError, match='max_snapshots_per_run'):
        FirestoreStepStore(firestore_client, media_store=None, max_snapshots_per_run=0)


class TestProtocol:
    async def test_pruned_snapshot_key_remains_suppressed(self, firestore_client: AsyncClient) -> None:
        store = _bounded(firestore_client, 1)
        older = ContinuableSnapshot(run_id='r1', step_index=1, messages=[], idempotency_key='0:1:complete')
        newer = ContinuableSnapshot(run_id='r1', step_index=2, messages=[], idempotency_key='1:2:complete')

        await store.save_snapshot(older)
        await store.save_snapshot(newer)
        await store.save_snapshot(older)

        assert await store.latest_snapshot(run_id='r1') == newer

    async def test_register_and_get_run(self, store: FirestoreStepStore) -> None:
        await store.register_run(RunRecord(run_id='r1', conversation_id='c1', agent_name='agent', metadata={'k': 'v'}))
        fetched = await store.get_run(run_id='r1')
        assert fetched is not None
        assert fetched.conversation_id == 'c1'
        assert fetched.metadata == {'k': 'v'}

    async def test_register_duplicate_run_raises_value_error(self, store: FirestoreStepStore) -> None:
        await store.register_run(RunRecord(run_id='r1'))
        with pytest.raises(ValueError, match="run_id 'r1' is already in the store"):
            await store.register_run(RunRecord(run_id='r1', agent_name='racing-run'))

    async def test_list_runs_chronological(self, store: FirestoreStepStore) -> None:
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        await store.register_run(RunRecord(run_id='r3', started_at=base + timedelta(seconds=3)))
        await store.register_run(RunRecord(run_id='r1', started_at=base + timedelta(seconds=1)))
        await store.register_run(RunRecord(run_id='r2', started_at=base + timedelta(seconds=2)))
        assert [r.run_id for r in await store.list_runs()] == ['r1', 'r2', 'r3']

    async def test_list_runs_filters(self, store: FirestoreStepStore) -> None:
        await store.register_run(RunRecord(run_id='r1', conversation_id='a', parent_run_id='p'))
        await store.register_run(RunRecord(run_id='r2', conversation_id='a', parent_run_id='q'))
        await store.register_run(RunRecord(run_id='r3', conversation_id='b', parent_run_id='p'))

        assert {r.run_id for r in await store.list_runs(conversation_id='a')} == {'r1', 'r2'}
        assert {r.run_id for r in await store.list_runs(parent_run_id='p')} == {'r1', 'r3'}
        assert [r.run_id for r in await store.list_runs(parent_run_id='p', conversation_id='a')] == ['r1']

    async def test_list_runs_sorts_by_instant_not_iso_string(self, store: FirestoreStepStore) -> None:
        early_instant = datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=5)))  # 2023-12-31T20:00Z
        late_instant = datetime(2024, 1, 1, 0, 30, 0, tzinfo=timezone.utc)
        await store.register_run(RunRecord(run_id='late', started_at=late_instant))
        await store.register_run(RunRecord(run_id='early', started_at=early_instant))
        assert [r.run_id for r in await store.list_runs()] == ['early', 'late']

    async def test_append_and_list_events(self, store: FirestoreStepStore) -> None:
        await store.register_run(RunRecord(run_id='r1'))
        await store.append_event(StepEvent(run_id='r1', kind='run_started', step_index=0))
        await store.append_event(
            StepEvent(run_id='r1', kind='tool_call_started', step_index=1, tool_call_id='t1', tool_name='add')
        )
        events = await store.list_events(run_id='r1')
        assert [e.kind for e in events] == ['run_started', 'tool_call_started']
        assert events[1].tool_call_id == 't1'

    async def test_save_and_load_snapshot(self, store: FirestoreStepStore) -> None:
        await store.register_run(RunRecord(run_id='r1'))
        messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content='hello')]),
            ModelResponse(parts=[TextPart(content='hi back')]),
        ]
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=2, messages=messages))
        snap = await store.latest_snapshot(run_id='r1')
        assert snap is not None
        assert snap.step_index == 2
        assert len(snap.messages) == 2

    async def test_latest_snapshot_default_skips_interrupted(self, store: FirestoreStepStore) -> None:
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=1, messages=_msgs(), state='interrupted'))

        default = await store.latest_snapshot(run_id='r1')
        assert default is not None and default.state == 'complete' and default.step_index == 0
        opted = await store.latest_snapshot(run_id='r1', include_interrupted=True)
        assert opted is not None and opted.state == 'interrupted' and opted.step_index == 1

    async def test_snapshot_seq_monotonic_across_reset_step(self, store: FirestoreStepStore) -> None:
        await store.register_run(RunRecord(run_id='r1'))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=5, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs()))
        snap = await store.latest_snapshot(run_id='r1')
        assert snap is not None
        assert snap.step_index == 0

    async def test_keyed_snapshot_replays_preserve_complete_and_interrupted_at_same_step(
        self, store: FirestoreStepStore
    ) -> None:
        complete = ContinuableSnapshot(
            run_id='r1', step_index=2, messages=_msgs(), state='complete', idempotency_key='2:complete'
        )
        interrupted = ContinuableSnapshot(
            run_id='r1', step_index=2, messages=_msgs(), state='interrupted', idempotency_key='2:interrupted'
        )

        await store.save_snapshot(complete)
        await store.save_snapshot(interrupted)
        await store.save_snapshot(complete)
        await store.save_snapshot(interrupted)

        snapshots = await store.list_snapshots(run_id='r1', include_interrupted=True)
        assert [(snapshot.step_index, snapshot.state) for snapshot in snapshots] == [
            (2, 'complete'),
            (2, 'interrupted'),
        ]
        assert await store.latest_snapshot(run_id='r1') == complete

    async def test_keyed_event_replay_is_suppressed_but_unkeyed_events_append(self, store: FirestoreStepStore) -> None:
        keyed = StepEvent(run_id='r1', kind='run_started', step_index=0, idempotency_key='event:0')
        await store.append_event(keyed)
        await store.append_event(keyed)
        await store.append_event(StepEvent(run_id='r1', kind='run_started', step_index=0))
        await store.append_event(StepEvent(run_id='r1', kind='run_started', step_index=0))
        assert len(await store.list_events(run_id='r1')) == 3

    async def test_tool_effect_upsert_and_scope(self, store: FirestoreStepStore) -> None:
        await store.record_tool_effect(ToolEffectRecord(tool_call_id='t1', tool_name='add', run_id='r1', status='started'))
        await store.record_tool_effect(
            ToolEffectRecord(tool_call_id='t1', tool_name='add', run_id='r1', status='completed', effect_summary='ok')
        )
        await store.record_tool_effect(ToolEffectRecord(tool_call_id='t1', tool_name='add', run_id='r2', status='started'))
        effect = await store.get_tool_effect(run_id='r1', tool_call_id='t1')
        assert effect is not None and effect.status == 'completed' and effect.effect_summary == 'ok'
        other = await store.get_tool_effect(run_id='r2', tool_call_id='t1')
        assert other is not None and other.status == 'started'

    async def test_list_unresolved_tool_effects(self, store: FirestoreStepStore) -> None:
        await store.record_tool_effect(ToolEffectRecord(tool_call_id='t1', tool_name='add', run_id='r1', status='started'))
        await store.record_tool_effect(
            ToolEffectRecord(tool_call_id='t2', tool_name='mul', run_id='r1', status='completed')
        )
        unresolved = await store.list_unresolved_tool_effects(run_id='r1')
        assert [r.tool_call_id for r in unresolved] == ['t1']

    async def test_missing_lookups_return_none_or_empty(self, store: FirestoreStepStore) -> None:
        assert await store.get_run(run_id='nope') is None
        assert await store.latest_snapshot(run_id='nope') is None
        assert await store.get_tool_effect(run_id='nope', tool_call_id='x') is None
        assert await store.list_events(run_id='nope') == []
        assert await store.list_unresolved_tool_effects(run_id='nope') == []
        assert await store.list_runs() == []

    async def test_non_ascii_metadata_round_trips(self, store: FirestoreStepStore) -> None:
        metadata = {'user': 'Ada Lovelace ✨', 'ключ': 'ジョブ完了', 'emoji': '🙂'}
        await store.register_run(RunRecord(run_id='r1', metadata=metadata))
        await store.append_event(StepEvent(run_id='r1', kind='run_started', step_index=0, metadata=metadata))

        run = await store.get_run(run_id='r1')
        assert run is not None
        assert run.metadata == metadata
        assert [e.metadata for e in await store.list_events(run_id='r1')] == [metadata]

    async def test_corrupted_snapshot_document_raises(self, firestore_client: AsyncClient) -> None:
        store = FirestoreStepStore(firestore_client, media_store=None)
        await store.register_run(RunRecord(run_id='r1'))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs('x')))
        async for doc in firestore_client.collection('steps_snapshots').stream():
            await doc.reference.update({'timestamp': 123})
        with pytest.raises(ValueError, match='snapshot document has wrong types'):
            await store.latest_snapshot(run_id='r1')


class TestListSnapshots:
    async def test_write_order_and_interrupted_filter(self, store: FirestoreStepStore) -> None:
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=2, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs(), state='interrupted'))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=1, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r2', step_index=9, messages=_msgs()))

        assert [s.step_index for s in await store.list_snapshots(run_id='r1')] == [2, 1]
        opted = await store.list_snapshots(run_id='r1', include_interrupted=True)
        assert [s.step_index for s in opted] == [2, 0, 1]
        assert [s.state for s in opted] == ['complete', 'interrupted', 'complete']
        assert await store.list_snapshots(run_id='nope') == []

    async def test_restores_externalized_media(self, firestore_client: AsyncClient, tmp_path: Path) -> None:
        store = FirestoreStepStore(
            firestore_client, media_store=DiskMediaStore(tmp_path / 'media'), media_threshold_bytes=1024
        )
        big_text = 'Z' * 5_000
        messages: list[ModelMessage] = [
            ModelRequest(parts=[ToolReturnPart(tool_name='scrape', content=big_text, tool_call_id='t1')])
        ]
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=messages))

        snapshots = await store.list_snapshots(run_id='r1')
        assert len(snapshots) == 1
        request = snapshots[0].messages[0]
        assert isinstance(request, ModelRequest)
        tool_return = request.parts[0]
        assert isinstance(tool_return, ToolReturnPart)
        assert tool_return.content == big_text

    async def test_store_is_accepted_as_a_search_substrate(self, store: FirestoreStepStore) -> None:
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs('remember this')))
        source = SnapshotHistorySource(store)
        assert [type(m).__name__ for m in await source.run_history(run_id='r1')] == ['ModelRequest']

    async def test_skips_unparsable_document(
        self, firestore_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = FirestoreStepStore(firestore_client, media_store=None)
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=1, messages=_msgs()))
        query = firestore_client.collection('steps_snapshots').where(filter=FieldFilter('step_index', '==', 0))
        async for doc in query.stream():
            await doc.reference.update({'timestamp': 123})

        with caplog.at_level(logging.WARNING):
            snapshots = await store.list_snapshots(run_id='r1')
        assert [s.step_index for s in snapshots] == [1]
        assert 'Skipping unparsable snapshot document for run r1' in caplog.text


class TestRetention:
    async def test_unbounded_keeps_every_snapshot(self, store: FirestoreStepStore) -> None:
        for step in range(4):
            await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=step, messages=_msgs()))
        assert [s.step_index for s in await store.list_snapshots(run_id='r1')] == [0, 1, 2, 3]

    async def test_prunes_to_the_newest_window(self, firestore_client: AsyncClient) -> None:
        store = _bounded(firestore_client, 2)
        for step in range(5):
            await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=step, messages=_msgs()))
        assert [s.step_index for s in await store.list_snapshots(run_id='r1')] == [3, 4]

    async def test_keeps_newest_complete_below_the_window(self, firestore_client: AsyncClient) -> None:
        store = _bounded(firestore_client, 2)
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs()))
        for step in (1, 2, 3):
            await store.save_snapshot(
                ContinuableSnapshot(run_id='r1', step_index=step, messages=_msgs(), state='interrupted')
            )

        retained = await store.list_snapshots(run_id='r1', include_interrupted=True)
        assert [s.step_index for s in retained] == [0, 2, 3]
        resumable = await store.latest_snapshot(run_id='r1')
        assert resumable is not None
        assert resumable.step_index == 0

    async def test_pruning_is_scoped_to_the_written_run(self, firestore_client: AsyncClient) -> None:
        store = _bounded(firestore_client, 1)
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r2', step_index=0, messages=_msgs()))
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=1, messages=_msgs()))

        assert [s.step_index for s in await store.list_snapshots(run_id='r1')] == [1]
        assert [s.step_index for s in await store.list_snapshots(run_id='r2')] == [0]

    async def test_pruning_deletes_message_chunks(self, firestore_client: AsyncClient) -> None:
        store = _bounded(firestore_client, 1)
        for step in range(3):
            await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=step, messages=_msgs()))
        parts = [part async for part in firestore_client.collection_group('parts').stream()]
        assert len(parts) == 1


class TestMedia:
    async def test_large_binary_and_text_externalized_and_restored(
        self, firestore_client: AsyncClient, tmp_path: Path
    ) -> None:
        media_dir = tmp_path / 'media'
        store = FirestoreStepStore(firestore_client, media_store=DiskMediaStore(media_dir), media_threshold_bytes=64 * 1024)
        await store.register_run(RunRecord(run_id='r1'))
        big_binary = b'\xab' * 100_000
        big_text = 'Z' * 100_000
        messages: list[ModelMessage] = [
            ModelRequest(
                parts=[
                    UserPromptPart(content=[BinaryContent(data=big_binary, media_type='image/png')]),
                    ToolReturnPart(tool_name='scrape', content=big_text, tool_call_id='t1'),
                ]
            ),
            ModelResponse(parts=[TextPart(content='done')]),
        ]
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=messages))

        assert len([path for path in media_dir.rglob('*') if path.is_file()]) == 2

        snap = await store.latest_snapshot(run_id='r1')
        assert snap is not None
        request = snap.messages[0]
        assert isinstance(request, ModelRequest)
        prompt = request.parts[0]
        assert isinstance(prompt, UserPromptPart)
        assert isinstance(prompt.content, list)
        binary = prompt.content[0]
        assert isinstance(binary, BinaryContent)
        assert binary.data == big_binary
        tool_return = request.parts[1]
        assert isinstance(tool_return, ToolReturnPart)
        assert tool_return.content == big_text

    async def test_below_threshold_stays_inline(self, firestore_client: AsyncClient, tmp_path: Path) -> None:
        media_dir = tmp_path / 'media'
        store = FirestoreStepStore(firestore_client, media_store=DiskMediaStore(media_dir), media_threshold_bytes=64 * 1024)
        await store.register_run(RunRecord(run_id='r1'))
        messages: list[ModelMessage] = [ModelResponse(parts=[TextPart(content='small')])]
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=messages))
        assert not media_dir.exists() or not any(path.is_file() for path in media_dir.rglob('*'))

        snap = await store.latest_snapshot(run_id='r1')
        assert snap is not None
        response = snap.messages[0]
        assert isinstance(response, ModelResponse)
        text = response.parts[0]
        assert isinstance(text, TextPart)
        assert text.content == 'small'


class TestFirestoreLimits:
    async def test_inline_snapshot_larger_than_a_document_is_chunked(self, store: FirestoreStepStore) -> None:
        big_text = 'Ü' * 1_500_000  # 3 MB of UTF-8: several chunks, a multibyte char split at boundaries
        messages: list[ModelMessage] = [
            ModelRequest(parts=[ToolReturnPart(tool_name='scrape', content=big_text, tool_call_id='t1')])
        ]
        await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=messages))
        snap = await store.latest_snapshot(run_id='r1')
        assert snap is not None
        tool_return = snap.messages[0].parts[0]
        assert isinstance(tool_return, ToolReturnPart)
        assert tool_return.content == big_text

    async def test_snapshot_over_transaction_limit_is_rejected(self, store: FirestoreStepStore) -> None:
        messages: list[ModelMessage] = [ModelResponse(parts=[TextPart(content='x' * (10 * 1024 * 1024))])]
        with pytest.raises(ValueError, match='too large to store'):
            await store.save_snapshot(ContinuableSnapshot(run_id='r1', step_index=0, messages=messages))
        assert await store.latest_snapshot(run_id='r1') is None

    async def test_concurrent_appends_get_distinct_ordered_sequence(self, store: FirestoreStepStore) -> None:
        await asyncio.gather(
            *(store.append_event(StepEvent(run_id='r1', kind='run_started', step_index=i)) for i in range(8))
        )
        events = await store.list_events(run_id='r1')
        assert sorted(e.step_index for e in events) == list(range(8))

    async def test_concurrent_saves_of_one_key_apply_once(self, store: FirestoreStepStore) -> None:
        snapshot = ContinuableSnapshot(run_id='r1', step_index=3, messages=_msgs(), idempotency_key='3:3:complete')
        await asyncio.gather(*(store.save_snapshot(snapshot) for _ in range(4)))
        assert await store.list_snapshots(run_id='r1') == [snapshot]
