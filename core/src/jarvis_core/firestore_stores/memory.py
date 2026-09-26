"""Firestore backend for the Pydantic AI Harness `Memory` capability.

Mirrors `PostgresMemoryStore`: each write or delete is one Firestore
transaction that checks the operation receipt, compares the stored version,
applies the change and records the receipt, so a replayed tool call returns
its original result and a stale write fails with `MemoryConflictError`.

Collections (named from `collection_prefix`):

- `<prefix>_files` -- one document per path; the document id is the SHA-256
  of the path so any valid memory path is a valid document id.
- `<prefix>_operations` -- one receipt per operation id.

Versions are random and never reused, so a version captured before a delete
cannot match a recreated file. Firestore limits a document to 1 MiB, which
bounds a single memory file.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from google.cloud.firestore import SERVER_TIMESTAMP, AsyncClient, AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

from pydantic_ai_harness.memory import (
    MemoryConflictError,
    MemoryFile,
    MemoryMutation,
    MemoryOperation,
    MemoryOperationConflictError,
    MemorySearchResult,
)
from pydantic_ai_harness.memory._store import (  # pyright: ignore[reportPrivateUsage]
    lexical_search,
    validate_store_path,
    validate_store_prefix,
)

from jarvis_core._firestore import TRANSACTION_ATTEMPTS

# Memory paths are ASCII (`[A-Za-z0-9_.-]` segments joined by `/`), so every
# path with a given prefix sorts below `prefix + '\x7f'`.
_PREFIX_END = '\x7f'
# Leaves room for the path, version and receipt fields under Firestore's 1 MiB document limit.
_MAX_CONTENT_BYTES = 1_000_000


def _doc_id(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _receipt(data: dict[str, Any], operation: MemoryOperation) -> MemoryMutation:
    if data['fingerprint'] != operation.fingerprint:
        raise MemoryOperationConflictError(f'operation id {operation.id!r} was reused with different arguments')
    return MemoryMutation(version=data['version'], replayed=True, existed=bool(data['existed']))


class FirestoreMemoryStore:
    """Transactional Firestore memory store with CAS and operation receipts."""

    def __init__(self, client: AsyncClient, *, collection_prefix: str = 'memory') -> None:
        self._client = client
        self._files = client.collection(f'{collection_prefix}_files')
        self._operations = client.collection(f'{collection_prefix}_operations')

    async def read(self, path: str, *, max_chars: int) -> MemoryFile | None:
        validate_store_path(path)
        if max_chars <= 0:
            raise ValueError('max_chars must be positive')
        snapshot = await self._files.document(_doc_id(path)).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        content: str = data['content']
        return MemoryFile(
            content=content[:max_chars],
            version=data['version'],
            operation_id=data.get('last_operation_id'),
            truncated=len(content) > max_chars,
        )

    async def get_operation(self, operation: MemoryOperation) -> MemoryMutation | None:
        snapshot = await self._operations.document(_doc_id(operation.id)).get()
        if not snapshot.exists:
            return None
        return _receipt(snapshot.to_dict() or {}, operation)

    async def write(
        self,
        path: str,
        content: str,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        validate_store_path(path)
        if len(content.encode('utf-8')) > _MAX_CONTENT_BYTES:
            raise ValueError(f'memory file {path!r} exceeds {_MAX_CONTENT_BYTES} bytes')
        return await self._mutate(path, content, expected_version=expected_version, operation=operation)

    async def delete(
        self,
        path: str,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        validate_store_path(path)
        return await self._mutate(path, None, expected_version=expected_version, operation=operation)

    async def _mutate(
        self,
        path: str,
        content: str | None,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None,
    ) -> MemoryMutation:
        file_ref = self._files.document(_doc_id(path))
        receipt_ref = self._operations.document(_doc_id(operation.id)) if operation else None
        action = 'written' if content is not None else 'deleted'

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> MemoryMutation:
            if receipt_ref is not None:
                assert operation is not None
                prior = await receipt_ref.get(transaction=transaction)
                if prior.exists:
                    return _receipt(prior.to_dict() or {}, operation)
            current = await file_ref.get(transaction=transaction)
            current_version = (current.to_dict() or {}).get('version') if current.exists else None
            if current_version != expected_version:
                raise MemoryConflictError(f'memory path {path!r} changed before it could be {action}')
            if content is not None:
                version: str | None = uuid4().hex
                transaction.set(
                    file_ref,
                    {
                        'path': path,
                        'content': content,
                        'version': version,
                        'last_operation_id': operation.id if operation else None,
                        # Listing fields for the owner's vault view; not part of the store protocol.
                        'chars': len(content),
                        'updated_at': SERVER_TIMESTAMP,
                    },
                )
            else:
                version = None
                if current.exists:
                    transaction.delete(file_ref)
            mutation = MemoryMutation(version=version, replayed=False, existed=current.exists)
            if receipt_ref is not None:
                assert operation is not None
                transaction.set(
                    receipt_ref,
                    {
                        'id': operation.id,
                        'fingerprint': operation.fingerprint,
                        'version': mutation.version,
                        'existed': mutation.existed,
                    },
                )
            return mutation

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    def _prefix_query(self, prefix: str):  # noqa: ANN202 - Firestore query type
        query = self._files.order_by('path')
        if prefix:
            query = self._files.where(filter=FieldFilter('path', '>=', prefix)).where(
                filter=FieldFilter('path', '<', prefix + _PREFIX_END)
            ).order_by('path')
        return query

    async def list_paths(self, prefix: str = '', *, limit: int) -> list[str]:
        validate_store_prefix(prefix)
        if limit <= 0:
            raise ValueError('limit must be positive')
        query = self._prefix_query(prefix).select(['path']).limit(limit)
        return [snapshot.get('path') async for snapshot in query.stream()]

    async def list_files(self, prefix: str = '', *, limit: int) -> list[dict[str, Any]]:
        """Path, version, size and last change of each file under `prefix`, for the owner's vault view.

        Files written before size tracking report their size from the content.
        """
        validate_store_prefix(prefix)
        if limit <= 0:
            raise ValueError('limit must be positive')
        files: list[dict[str, Any]] = []
        query = self._prefix_query(prefix).select(['path', 'version', 'chars', 'updated_at']).limit(limit)
        async for snapshot in query.stream():
            data = snapshot.to_dict() or {}
            chars = data.get('chars')
            if chars is None:
                full = await self._files.document(snapshot.id).get()
                chars = len((full.to_dict() or {}).get('content', ''))
            files.append({'path': data['path'], 'version': data['version'], 'chars': chars,
                          'updated_at': data.get('updated_at')})
        return files

    async def read_all(self, prefix: str, *, limit: int) -> list[tuple[str, str]]:
        """`(path, content)` of every file under `prefix`, for the owner's export."""
        validate_store_prefix(prefix)
        query = self._prefix_query(prefix).select(['path', 'content']).limit(limit)
        return [(snapshot.get('path'), snapshot.get('content')) async for snapshot in query.stream()]

    async def search(
        self,
        prefix: str,
        query: str,
        *,
        limit: int,
        max_files: int,
        max_chars: int,
        max_file_chars: int,
    ) -> MemorySearchResult:
        validate_store_prefix(prefix)
        if not query.split() or limit <= 0 or max_files <= 0 or max_chars <= 0 or max_file_chars <= 0:
            return MemorySearchResult(matches=[], scanned=0, truncated=False)
        rows = [
            (snapshot.get('path'), snapshot.get('content'))
            async for snapshot in self._prefix_query(prefix).select(['path', 'content']).limit(max_files + 1).stream()
        ]
        result = lexical_search(
            [(path, content[:max_file_chars]) for path, content in rows],
            query,
            limit=limit,
            max_files=max_files,
            max_chars=max_chars,
            score_prefix=prefix,
        )
        return MemorySearchResult(
            matches=result.matches,
            scanned=result.scanned,
            truncated=result.truncated or any(len(content) > max_file_chars for _, content in rows),
        )
