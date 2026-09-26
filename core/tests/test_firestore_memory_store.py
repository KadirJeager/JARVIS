"""Memory store contract tests for `FirestoreMemoryStore` on the Firestore emulator.

The contract tests are ported from pydantic-ai-harness v0.35.0
`tests/memory/test_stores.py` (MIT License, Copyright (c) Pydantic Services
Inc.), with the bundled stores replaced by the Firestore store. The last
tests cover Firestore-specific behavior: concurrent compare-and-set and
paths that would be reserved Firestore document ids.
"""

from __future__ import annotations

import asyncio

import pytest
from google.cloud.firestore import AsyncClient

from jarvis_core.firestore_stores import FirestoreMemoryStore
from pydantic_ai_harness.memory import (
    MemoryConflictError,
    MemoryOperation,
    MemoryOperationConflictError,
    MemoryStore,
    SearchableMemoryStore,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def store(firestore_client: AsyncClient) -> FirestoreMemoryStore:
    return FirestoreMemoryStore(firestore_client)


async def test_compare_and_set_contract(store: FirestoreMemoryStore) -> None:
    created = await store.write('notes/main.md', 'one', expected_version=None)
    assert created.version is not None
    assert not created.replayed
    assert not created.existed
    file = await store.read('notes/main.md', max_chars=1_000)
    assert file is not None
    assert file.content == 'one'
    assert file.version == created.version
    assert file.operation_id is None

    updated = await store.write('notes/main.md', 'two', expected_version=file.version)
    assert updated.version is not None
    assert updated.version != created.version
    assert updated.existed
    with pytest.raises(MemoryConflictError):
        await store.write('notes/main.md', 'stale', expected_version=file.version)
    with pytest.raises(MemoryConflictError):
        await store.delete('notes/main.md', expected_version=file.version)

    deleted = await store.delete('notes/main.md', expected_version=updated.version)
    assert deleted.version is None
    assert deleted.existed
    assert await store.read('notes/main.md', max_chars=1_000) is None


async def test_versions_do_not_repeat_after_delete_and_recreate(store: FirestoreMemoryStore) -> None:
    first = await store.write('main.md', 'same', expected_version=None)
    await store.delete('main.md', expected_version=first.version)
    recreated = await store.write('main.md', 'same', expected_version=None)

    assert recreated.version != first.version
    with pytest.raises(MemoryConflictError):
        await store.write('main.md', 'stale', expected_version=first.version)
    with pytest.raises(MemoryConflictError):
        await store.delete('main.md', expected_version=first.version)


async def test_read_and_listing_bounds(store: FirestoreMemoryStore) -> None:
    created = await store.write('a.md', '0123456789', expected_version=None)
    await store.write('b.md', 'b', expected_version=None)
    await store.write('c.md', 'c', expected_version=None)

    bounded = await store.read('a.md', max_chars=4)
    assert bounded is not None
    assert bounded.content == '0123'
    assert bounded.version == created.version
    assert bounded.truncated
    complete = await store.read('a.md', max_chars=20)
    assert complete is not None
    assert complete.version == created.version
    assert not complete.truncated
    assert await store.list_paths(limit=2) == ['a.md', 'b.md']


async def test_operation_receipts(store: FirestoreMemoryStore) -> None:
    operation = MemoryOperation(id='run-1:call-1', fingerprint='write:notes/main.md:one')

    first = await store.write('notes/main.md', 'one', expected_version=None, operation=operation)
    replay = await store.write('notes/main.md', 'one', expected_version=None, operation=operation)
    assert replay.version == first.version
    assert replay.replayed
    assert not replay.existed
    assert await store.get_operation(operation) == replay
    file = await store.read('notes/main.md', max_chars=1_000)
    assert file is not None
    assert file.operation_id == operation.id

    with pytest.raises(MemoryOperationConflictError):
        await store.get_operation(MemoryOperation(id=operation.id, fingerprint='different'))

    delete_operation = MemoryOperation(id='run-1:call-2', fingerprint='delete:missing.md')
    deleted = await store.delete('missing.md', expected_version=None, operation=delete_operation)
    assert not deleted.existed
    assert not deleted.replayed
    assert (await store.delete('missing.md', expected_version=None, operation=delete_operation)).replayed


async def test_scoped_bounded_search(store: FirestoreMemoryStore) -> None:
    for path, content in (
        ('tenant-a/main/alpha.md', 'alpha alpha'),
        ('tenant-a/main/beta.md', 'alpha'),
        ('tenant-a/main/other.md', 'unrelated'),
        ('tenant-b/main/private.md', 'alpha alpha alpha'),
    ):
        await store.write(path, content, expected_version=None)

    result = await store.search('tenant-a/main/', 'alpha', limit=10, max_files=10, max_chars=80, max_file_chars=1_000)
    assert [match.path for match in result.matches] == [
        'tenant-a/main/alpha.md',
        'tenant-a/main/beta.md',
    ]
    assert result.scanned == 3
    assert not result.truncated
    assert sum(len(match.path) + len(match.snippet) for match in result.matches) <= 80

    bounded = await store.search('tenant-a/main/', 'alpha', limit=10, max_files=1, max_chars=80, max_file_chars=1_000)
    assert bounded.scanned == 1
    assert bounded.truncated
    tiny = await store.search('tenant-a/main/', 'alpha', limit=10, max_files=10, max_chars=1, max_file_chars=1_000)
    assert tiny.matches == []
    assert tiny.truncated

    for query, limit, max_files, max_chars in (
        ('', 10, 10, 80),
        ('alpha', 0, 10, 80),
        ('alpha', 10, 0, 80),
        ('alpha', 10, 10, 0),
    ):
        empty = await store.search('', query, limit=limit, max_files=max_files, max_chars=max_chars, max_file_chars=1_000)
        assert empty.matches == []
        assert empty.scanned == 0
        assert not empty.truncated


async def test_search_snippets_cover_tiny_and_offset_windows(store: FirestoreMemoryStore) -> None:
    await store.write('a', '012345alpha-tail', expected_version=None)

    tiny = await store.search('', 'alpha', limit=1, max_files=1, max_chars=3, max_file_chars=1_000)
    assert len(tiny.matches) == 1
    assert len(tiny.matches[0].snippet) == 2
    offset = await store.search('', 'alpha', limit=1, max_files=1, max_chars=12, max_file_chars=1_000)
    assert len(offset.matches) == 1
    assert offset.matches[0].snippet.startswith('...')


async def test_search_bounds_each_file_and_ignores_namespace_prefix(store: FirestoreMemoryStore) -> None:
    namespace = 'n' * 180
    prefix = f'{namespace}/main/'
    await store.write(f'{prefix}note.md', 'prefix TARGET', expected_version=None)

    bounded = await store.search(prefix, 'target', limit=10, max_files=10, max_chars=100, max_file_chars=6)
    assert bounded.matches == []
    namespace_result = await store.search(prefix, namespace, limit=10, max_files=10, max_chars=100, max_file_chars=100)
    assert namespace_result.matches == []
    visible = await store.search(prefix, 'target', limit=10, max_files=10, max_chars=20, max_file_chars=100)
    assert [match.path for match in visible.matches] == [f'{prefix}note.md']


@pytest.mark.parametrize('path', ('../escape.md', '/absolute.md', 'a//b.md', 'a/../../b.md', 'a b.md'))
async def test_rejects_unsafe_paths(store: FirestoreMemoryStore, path: str) -> None:
    with pytest.raises(ValueError):
        await store.read(path, max_chars=1_000)


def test_implements_public_protocols(store: FirestoreMemoryStore) -> None:
    assert isinstance(store, MemoryStore)
    assert isinstance(store, SearchableMemoryStore)


async def test_rejects_non_positive_read_and_listing_bounds(store: FirestoreMemoryStore) -> None:
    with pytest.raises(ValueError, match='max_chars'):
        await store.read('main.md', max_chars=0)
    with pytest.raises(ValueError, match='limit'):
        await store.list_paths(limit=0)


async def test_concurrent_writers_with_same_expected_version(store: FirestoreMemoryStore) -> None:
    base = await store.write('shared.md', 'base', expected_version=None)
    results = await asyncio.gather(
        *(store.write('shared.md', f'writer-{i}', expected_version=base.version) for i in range(4)),
        return_exceptions=True,
    )
    succeeded = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, MemoryConflictError)]
    assert len(succeeded) == 1
    assert len(conflicts) == 3
    current = await store.read('shared.md', max_chars=100)
    assert current is not None and current.version == succeeded[0].version


async def test_concurrent_replays_of_one_operation_apply_once(store: FirestoreMemoryStore) -> None:
    operation = MemoryOperation(id='run-9:call-1', fingerprint='write:once.md:x')
    results = await asyncio.gather(
        *(store.write('once.md', 'x', expected_version=None, operation=operation) for _ in range(4))
    )
    assert len({result.version for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1


async def test_paths_that_look_like_reserved_document_ids(store: FirestoreMemoryStore) -> None:
    for path in ('__reserved__', '.', 'a/__b__/c.md'):
        created = await store.write(path, path, expected_version=None)
        file = await store.read(path, max_chars=100)
        assert file is not None and file.content == path and file.version == created.version
    assert await store.list_paths(limit=10) == ['.', '__reserved__', 'a/__b__/c.md']
