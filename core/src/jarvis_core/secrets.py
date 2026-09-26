"""Resolve secret references from settings to secret values at the point of use.

Settings store references, never values. Two schemes are supported:

- `env:NAME` -- an environment variable of this process (local development,
  or a variable the deployment injects from its own secret store).
- `projects/<p>/secrets/<name>/versions/<v>` -- a Secret Manager version,
  read with the service's own credentials.

Anything else is rejected rather than guessed. Values are cached briefly per
process so a turn does not call Secret Manager for every model request, and
rotation takes effect within the cache lifetime.

`SecretCatalog` lists secret names for the panel and stores keys the owner
enters, so a connection can be set up without a console.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud.secretmanager_v1 import SecretManagerServiceAsyncClient

_SECRET_VERSION_RE = re.compile(r'projects/[^/]+/secrets/[^/]+/versions/[^/]+')


class SecretResolutionError(RuntimeError):
    """A reference is malformed or names a secret that is not available."""


class SecretResolver:
    def __init__(self, *, cache_seconds: float = 300.0) -> None:
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, str]] = {}
        self._secret_manager: SecretManagerServiceAsyncClient | None = None

    def forget(self, ref: str) -> None:
        """Drop a cached value, e.g. after the owner stored a new version under the same reference."""
        self._cache.pop(ref, None)

    async def resolve(self, ref: str | None) -> str | None:
        if ref is None:
            return None
        cached = self._cache.get(ref)
        if cached is not None and time.monotonic() - cached[0] < self._cache_seconds:
            return cached[1]
        value = await self._load(ref)
        self._cache[ref] = (time.monotonic(), value)
        return value

    async def _load(self, ref: str) -> str:
        if ref.startswith('env:'):
            name = ref.removeprefix('env:')
            value = os.environ.get(name)
            if not name or value is None or not value.strip():
                raise SecretResolutionError(f'environment variable {name!r} is not set')
            return value.strip()
        if _SECRET_VERSION_RE.fullmatch(ref):
            if self._secret_manager is None:
                self._secret_manager = SecretManagerServiceAsyncClient()
            response = await self._secret_manager.access_secret_version(name=ref)
            return response.payload.data.decode('utf-8').strip()
        raise SecretResolutionError('unsupported secret reference scheme')


_SECRET_SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')


class SecretCatalog:
    """Secret names for the panel's picker, and owner-entered secrets under one name prefix.

    Values only flow in: the owner pastes a key once, it becomes a Secret
    Manager version, and settings keep its reference. Nothing here returns a
    secret value. The service may create and rotate only secrets whose id
    starts with `prefix`; the deployment grants exactly that with an IAM
    condition, plus listing of names across the project.
    """

    def __init__(self, project_id: str, prefix: str, client: SecretManagerServiceAsyncClient | None = None) -> None:
        self._project = f'projects/{project_id}'
        self._prefix = prefix
        self._client = client

    def _secrets(self) -> SecretManagerServiceAsyncClient:
        if self._client is None:
            self._client = SecretManagerServiceAsyncClient()
        return self._client

    async def list(self) -> list[dict[str, Any]]:
        pager = await self._secrets().list_secrets(parent=self._project)
        items = []
        async for secret in pager:
            secret_id = secret.name.rsplit('/', 1)[-1]
            items.append({
                'id': secret_id,
                'ref': f'{secret.name}/versions/latest',
                'managed': secret_id.startswith(self._prefix),
                'created_at': secret.create_time.isoformat() if secret.create_time else None,
            })
        return sorted(items, key=lambda item: item['id'])

    def managed_id(self, slug: str) -> str:
        if not _SECRET_SLUG.fullmatch(slug):
            raise ValueError('name must be lowercase letters, digits and dashes')
        return f'{self._prefix}{slug}'

    async def put(self, slug: str, value: str) -> str:
        """Create the managed secret if needed and add `value` as its newest version; return its reference."""
        secret_id = self.managed_id(slug)
        name = f'{self._project}/secrets/{secret_id}'
        client = self._secrets()
        try:
            await client.get_secret(name=name)
        except NotFound:
            await client.create_secret(
                parent=self._project,
                secret_id=secret_id,
                secret={'replication': {'automatic': {}}, 'labels': {'managed-by': 'jarvis'}},
            )
        await client.add_secret_version(parent=name, payload={'data': value.encode('utf-8')})
        return f'{name}/versions/latest'

    async def delete(self, slug: str) -> None:
        await self._secrets().delete_secret(name=f'{self._project}/secrets/{self.managed_id(slug)}')
