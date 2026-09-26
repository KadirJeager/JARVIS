"""Firestore backend for the Pydantic AI Harness `StepPersistence` capability.

Mirrors `MongoStepStore`: an append-only event log, continuable snapshots and
a tool-effect ledger. Differences forced by Firestore:

- Sequence numbers come from one counter document per run and kind, advanced
  with a server-side `Increment` whose result the commit returns. Parallel
  tool calls append events concurrently, and a read-modify-write transaction
  on the counter would abort under that contention. Numbers may have gaps;
  ordering is only defined within a run, which is all the contract reads.
- A document is limited to 1 MiB, so snapshot messages are stored as ordered
  byte chunks in a `parts` subcollection, committed in one batch with the
  snapshot document. Large binary and text parts are still externalized to
  the `MediaStore` first, as in the other stores.
- Keyed events and snapshot keys are written with `create`, so a replayed
  key fails the whole batch atomically instead of relying on a unique index.

Collections (named from `collection_prefix`): `<p>_runs`, `<p>_events`,
`<p>_snapshots` (+ `parts`), `<p>_snapshot_keys`, `<p>_tool_effects`,
`<p>_counters`. Queries filter by `run_id` and order by `seq`; deployed
databases need the composite indexes listed in `deploy/firestore.indexes.json`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from google.api_core.exceptions import Aborted, AlreadyExists
from google.api_core.retry import if_exception_type
from google.api_core.retry_async import AsyncRetry
from google.cloud.firestore import AsyncClient, Increment
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from pydantic_ai_harness.media import MediaStore, externalize_media, restore_media
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    SnapshotState,
    StepEvent,
    ToolEffectRecord,
)
from pydantic_ai_harness.step_persistence._store import (  # pyright: ignore[reportPrivateUsage]
    _DEFAULT_MEDIA_THRESHOLD_BYTES,
    _event_from_dict,
    _event_to_dict,
    _opt_str,
    _retained_seqs,
    _run_from_dict,
    _run_to_dict,
    _snapshot_state,
    _tool_effect_from_dict,
    _tool_effect_to_dict,
    _validate_max_snapshots,
)

_logger = logging.getLogger(__name__)

# Below Firestore's 1 MiB document limit with room for the chunk's own fields.
_CHUNK_BYTES = 900_000
# The snapshot, its chunks and its key share one batch, and Firestore caps a
# commit request at 10 MiB.
_MAX_SNAPSHOT_BYTES = 9 * 1024 * 1024
# Writes that contend on one document (a counter, a replayed key) can be
# rejected with ABORTED, which means nothing was applied; Firestore's guidance
# is to retry with backoff. A retried `create` that lost the race then fails
# with `AlreadyExists` and is treated as an applied replay.
_RETRY_ON_CONTENTION = AsyncRetry(predicate=if_exception_type(Aborted), initial=0.05, maximum=2.0, timeout=60.0)


def _doc_id(*parts: str) -> str:
    return hashlib.sha256('\x00'.join(parts).encode('utf-8')).hexdigest()


class FirestoreStepStore:
    """Firestore-backed step-persistence store; see the module docstring for layout.

    `media_store` receives binary and text parts at or above
    `media_threshold_bytes`; pass `None` to keep them inline (bounded by the
    chunked snapshot size). `max_snapshots_per_run` bounds per-run snapshot
    growth with the same retain rule as the bundled stores.
    """

    def __init__(
        self,
        client: AsyncClient,
        *,
        media_store: MediaStore | None,
        collection_prefix: str = 'steps',
        media_threshold_bytes: int = _DEFAULT_MEDIA_THRESHOLD_BYTES,
        max_snapshots_per_run: int | None = None,
    ) -> None:
        _validate_max_snapshots(max_snapshots_per_run)
        self._client = client
        self._media_store = media_store
        self._media_threshold_bytes = media_threshold_bytes
        self._max_snapshots_per_run = max_snapshots_per_run
        self._runs = client.collection(f'{collection_prefix}_runs')
        self._events = client.collection(f'{collection_prefix}_events')
        self._snapshots = client.collection(f'{collection_prefix}_snapshots')
        self._snapshot_keys = client.collection(f'{collection_prefix}_snapshot_keys')
        self._tool_effects = client.collection(f'{collection_prefix}_tool_effects')
        self._counters = client.collection(f'{collection_prefix}_counters')

    # -- runs ---------------------------------------------------------------

    async def register_run(self, record: RunRecord) -> None:
        try:
            await self._runs.document(_doc_id(record.run_id)).create(_run_to_dict(record))
        except AlreadyExists as exc:
            raise ValueError(f'run_id {record.run_id!r} is already in the store') from exc

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        snapshot = await self._runs.document(_doc_id(run_id)).get()
        return _run_from_dict(snapshot.to_dict() or {}) if snapshot.exists else None

    async def list_runs(
        self,
        *,
        parent_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[RunRecord]:
        query: Any = self._runs
        if parent_run_id is not None:
            query = query.where(filter=FieldFilter('parent_run_id', '==', parent_run_id))
        if conversation_id is not None:
            query = query.where(filter=FieldFilter('conversation_id', '==', conversation_id))
        # Sort by the parsed instant, not the stored ISO string, as the protocol requires.
        records = [_run_from_dict(snapshot.to_dict() or {}) async for snapshot in query.stream()]
        return sorted(records, key=lambda record: record.started_at)

    # -- events -------------------------------------------------------------

    async def _allocate_seq(self, name: str, run_id: str) -> int:
        batch = self._client.batch()
        batch.set(self._counters.document(_doc_id(name, run_id)), {'seq': Increment(1)}, merge=True)
        (result,) = await batch.commit(retry=_RETRY_ON_CONTENTION)
        return int(result.transform_results[0].integer_value)

    async def append_event(self, event: StepEvent) -> None:
        data = {**_event_to_dict(event), 'seq': await self._allocate_seq('events', event.run_id)}
        if event.idempotency_key is None:
            await self._events.document().set(data)
            return
        try:
            await self._events.document(_doc_id(event.run_id, event.idempotency_key)).create(
                data, retry=_RETRY_ON_CONTENTION
            )
        except AlreadyExists:
            return

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        query = self._events.where(filter=FieldFilter('run_id', '==', run_id)).order_by('seq')
        return [_event_from_dict(snapshot.to_dict() or {}) async for snapshot in query.stream()]

    # -- snapshots ----------------------------------------------------------

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        messages_json: object = json.loads(ModelMessagesTypeAdapter.dump_json(snapshot.messages).decode('utf-8'))
        if self._media_store is not None:
            messages_json = await externalize_media(
                messages_json,
                media_store=self._media_store,
                threshold_bytes=self._media_threshold_bytes,
            )
        payload = json.dumps(messages_json).encode('utf-8')
        if len(payload) > _MAX_SNAPSHOT_BYTES:
            raise ValueError(f'snapshot for run {snapshot.run_id!r} is too large to store ({len(payload)} bytes)')
        chunks = [payload[i : i + _CHUNK_BYTES] for i in range(0, len(payload), _CHUNK_BYTES)] or [b'']

        key = snapshot.idempotency_key
        key_ref = self._snapshot_keys.document(_doc_id(snapshot.run_id, key)) if key is not None else None
        # The key ledger outlives pruned snapshot documents, so a pruned key stays applied.
        if key_ref is not None and (await key_ref.get()).exists:
            return
        snapshot_ref = self._snapshots.document()
        document = {
            'run_id': snapshot.run_id,
            'step_index': snapshot.step_index,
            'conversation_id': snapshot.conversation_id,
            'parent_run_id': snapshot.parent_run_id,
            'agent_name': snapshot.agent_name,
            'timestamp': snapshot.timestamp.isoformat(),
            'state': snapshot.state,
            'idempotency_key': key,
            'chunks': len(chunks),
            'seq': await self._allocate_seq('snapshots', snapshot.run_id),
        }
        batch = self._client.batch()
        if key_ref is not None:
            # A concurrent save of the same key makes this `create` fail the whole batch.
            batch.create(key_ref, {'run_id': snapshot.run_id, 'key': key})
        batch.set(snapshot_ref, document)
        for index, chunk in enumerate(chunks):
            batch.set(snapshot_ref.collection('parts').document(f'{index:06d}'), {'data': chunk})
        try:
            await batch.commit(retry=_RETRY_ON_CONTENTION)
        except AlreadyExists:
            return
        await self._prune_snapshots(snapshot.run_id)

    async def _prune_snapshots(self, run_id: str) -> None:
        """Delete this run's snapshots outside the shared retain set when bounded.

        Externalized media is content-addressed and may be shared, so pruning
        never deletes blobs, as in the bundled stores.
        """
        if self._max_snapshots_per_run is None:
            return
        documents = [
            snapshot
            async for snapshot in self._snapshots.where(filter=FieldFilter('run_id', '==', run_id))
            .select(['seq', 'state'])
            .stream()
        ]
        entries = [(int(doc.get('seq')), _snapshot_state(doc.get('state'))) for doc in documents]
        retained = _retained_seqs(entries, self._max_snapshots_per_run)
        for doc in documents:
            if int(doc.get('seq')) in retained:
                continue
            batch = self._client.batch()
            async for part in doc.reference.collection('parts').select([]).stream():
                batch.delete(part.reference)
            batch.delete(doc.reference)
            await batch.commit()

    async def _snapshot_from_doc(self, run_id: str, doc: Any) -> ContinuableSnapshot:
        data: dict[str, Any] = doc.to_dict() or {}
        step_index = data.get('step_index')
        timestamp_raw = data.get('timestamp')
        chunk_count = data.get('chunks')
        if not (isinstance(step_index, int) and isinstance(timestamp_raw, str) and isinstance(chunk_count, int)):
            raise ValueError('snapshot document has wrong types')
        parts = sorted([part async for part in doc.reference.collection('parts').stream()], key=lambda part: part.id)
        if len(parts) != chunk_count:
            raise ValueError('snapshot document has missing message chunks')
        messages_json: object = json.loads(b''.join(part.get('data') for part in parts).decode('utf-8'))
        if self._media_store is not None:
            messages_json = await restore_media(messages_json, media_store=self._media_store)
        messages: list[ModelMessage] = ModelMessagesTypeAdapter.validate_python(messages_json)
        return ContinuableSnapshot(
            run_id=run_id,
            step_index=step_index,
            messages=messages,
            conversation_id=_opt_str(data.get('conversation_id')),
            parent_run_id=_opt_str(data.get('parent_run_id')),
            agent_name=_opt_str(data.get('agent_name')),
            timestamp=datetime.fromisoformat(timestamp_raw),
            state=_snapshot_state(data.get('state')),
            idempotency_key=_opt_str(data.get('idempotency_key')),
        )

    def _snapshot_query(self, run_id: str, include_interrupted: bool) -> Any:
        query: Any = self._snapshots.where(filter=FieldFilter('run_id', '==', run_id))
        if not include_interrupted:
            state: SnapshotState = 'complete'
            query = query.where(filter=FieldFilter('state', '==', state))
        return query

    async def latest_snapshot(self, *, run_id: str, include_interrupted: bool = False) -> ContinuableSnapshot | None:
        query = self._snapshot_query(run_id, include_interrupted).order_by('seq', direction='DESCENDING').limit(1)
        async for doc in query.stream():
            return await self._snapshot_from_doc(run_id, doc)
        return None

    async def list_snapshots(self, *, run_id: str, include_interrupted: bool = False) -> list[ContinuableSnapshot]:
        """Return retained snapshots for `run_id` in write order.

        Not part of the `StepStore` protocol; `conversation_search` uses it.
        Unparsable documents are skipped and logged, as in the bundled stores.
        """
        snapshots: list[ContinuableSnapshot] = []
        async for doc in self._snapshot_query(run_id, include_interrupted).order_by('seq').stream():
            try:
                snapshots.append(await self._snapshot_from_doc(run_id, doc))
            except Exception:
                _logger.warning('Skipping unparsable snapshot document for run %s', run_id, exc_info=True)
        return snapshots

    # -- tool effects -------------------------------------------------------

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        await self._tool_effects.document(_doc_id(record.run_id, record.tool_call_id)).set(
            _tool_effect_to_dict(record)
        )

    async def get_tool_effect(self, *, run_id: str, tool_call_id: str) -> ToolEffectRecord | None:
        snapshot = await self._tool_effects.document(_doc_id(run_id, tool_call_id)).get()
        return _tool_effect_from_dict(snapshot.to_dict() or {}) if snapshot.exists else None

    async def list_unresolved_tool_effects(self, *, run_id: str) -> list[ToolEffectRecord]:
        query = self._tool_effects.where(filter=FieldFilter('run_id', '==', run_id)).where(
            filter=FieldFilter('status', '==', 'started')
        )
        return [_tool_effect_from_dict(snapshot.to_dict() or {}) async for snapshot in query.stream()]
