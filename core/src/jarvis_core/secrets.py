"""Resolve secret references from settings to secret values at the point of use.

Settings store references, never values. Two schemes are supported:

- `env:NAME` -- an environment variable of this process (local development,
  or a variable the deployment injects from its own secret store).
- `projects/<p>/secrets/<name>/versions/<v>` -- a Secret Manager version,
  read with the service's own credentials.

Anything else is rejected rather than guessed. Values are cached briefly per
process so a turn does not call Secret Manager for every model request, and
rotation takes effect within the cache lifetime.
"""

from __future__ import annotations

import os
import re
import time

from google.cloud.secretmanager_v1 import SecretManagerServiceAsyncClient

_SECRET_VERSION_RE = re.compile(r'projects/[^/]+/secrets/[^/]+/versions/[^/]+')


class SecretResolutionError(RuntimeError):
    """A reference is malformed or names a secret that is not available."""


class SecretResolver:
    def __init__(self, *, cache_seconds: float = 300.0) -> None:
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, str]] = {}
        self._secret_manager: SecretManagerServiceAsyncClient | None = None

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
