"""Versioned per-user assistant settings stored in Firestore.

Settings hold references to secrets (see `jarvis_core.secrets`), never secret
values. Every save is a compare-and-set on the current version and appends the
saved version to a history collection, so a turn can record which settings
version it ran with and the panel can show what changed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from google.cloud.firestore import AsyncClient, AsyncTransaction, async_transactional
from pydantic import BaseModel, Field, field_validator

from jarvis_core._firestore import TRANSACTION_ATTEMPTS

_LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


class EndpointConnection(BaseModel):
    """How to reach a model endpoint: wire protocol, base URL and credentials reference."""

    protocol: Literal['google', 'openai']
    """`google` speaks the Gemini API; `openai` speaks OpenAI Chat Completions."""
    base_url: str | None = None
    """Endpoint base URL; `None` uses the provider's public default."""
    api_key_ref: str | None = None
    """Secret reference (`env:NAME` or a Secret Manager version name); `None` for keyless local servers."""

    @field_validator('base_url')
    @classmethod
    def _reachable_endpoint_only(cls, value: str | None) -> str | None:
        """Allow HTTPS anywhere and plain HTTP only on loopback (e.g. a sidecar proxy).

        The server fetches this URL, so plain HTTP to other hosts would let a
        setting reach internal networks or the cloud metadata server.
        """
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
            raise ValueError('base_url must be a plain URL without credentials, query or fragment')
        if parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname in _LOOPBACK_HOSTS):
            return value.rstrip('/')
        raise ValueError('base_url must use https, or http on a loopback host')


class ModelConnection(EndpointConnection):
    """An endpoint plus the conversation model chosen from its live catalog."""

    model: str = Field(min_length=1)


class ToolSelection(BaseModel):
    """How deferred tools are chosen for a turn."""

    strategy: Literal['builtin', 'typesafe_jev'] = 'builtin'
    """`builtin` uses Pydantic AI's own tool search; `typesafe_jev` asks TypeSafe Jev."""
    jev_model: str = 'jev-latest'
    api_key_ref: str | None = None


class TurnLimits(BaseModel):
    request_limit: int = Field(default=12, ge=1)
    tool_calls_limit: int = Field(default=16, ge=0)


class AssistantSettings(BaseModel):
    """Everything a turn needs besides the conversation itself."""

    instructions: str = ''
    """Persona and standing guidance written by the owner."""
    model: ModelConnection
    tool_selection: ToolSelection = ToolSelection()
    limits: TurnLimits = TurnLimits()


class StoredSettings(BaseModel):
    version: int
    settings: AssistantSettings
    updated_at: datetime


class SettingsConflictError(RuntimeError):
    """The stored settings version changed since it was read."""


class FirestoreSettingsStore:
    """`users/{uid}/settings/current` plus `users/{uid}/settings_history/{version}`."""

    def __init__(self, client: AsyncClient) -> None:
        self._users = client.collection('users')
        self._client = client

    async def get(self, uid: str) -> StoredSettings | None:
        snapshot = await self._users.document(uid).collection('settings').document('current').get()
        return StoredSettings.model_validate(snapshot.to_dict()) if snapshot.exists else None

    async def get_version(self, uid: str, version: int) -> StoredSettings | None:
        snapshot = await self._users.document(uid).collection('settings_history').document(str(version)).get()
        return StoredSettings.model_validate(snapshot.to_dict()) if snapshot.exists else None

    async def save(self, uid: str, settings: AssistantSettings, *, expected_version: int | None) -> StoredSettings:
        """Store `settings` if the current version equals `expected_version` (`None` = not yet set)."""
        user = self._users.document(uid)
        current_ref = user.collection('settings').document('current')
        history = user.collection('settings_history')

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> StoredSettings:
            snapshot = await current_ref.get(transaction=transaction)
            version = int((snapshot.to_dict() or {})['version']) if snapshot.exists else None
            if version != expected_version:
                raise SettingsConflictError(f'settings changed: expected version {expected_version}, found {version}')
            stored = StoredSettings(
                version=(version or 0) + 1, settings=settings, updated_at=datetime.now(timezone.utc)
            )
            data = stored.model_dump(mode='json')
            transaction.set(current_ref, data)
            transaction.set(history.document(str(stored.version)), data)
            return stored

        return await apply(self._client.transaction(max_attempts=TRANSACTION_ATTEMPTS))
