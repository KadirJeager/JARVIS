"""HTTP surface of the core service.

People call `/v1/*` with a Firebase ID token; Cloud Tasks and Cloud Scheduler
call `/internal/*` with a Google-signed OIDC token. The service holds no
conversation state in memory: every request reads and writes Firestore, so
instances can scale to zero between events. Clients read conversations
directly from Firestore under security rules and do not keep a connection to
this service open.

Annotations are evaluated eagerly here (no `from __future__ import
annotations`): FastAPI resolves route annotations when routes are declared,
and the dependency aliases are local to `create_app`.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path as FilePath
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import GoogleAPICallError
from pydantic_ai_harness.memory import MemoryConflictError
from pydantic_ai_harness.memory._store import validate_store_path  # pyright: ignore[reportPrivateUsage]
from google.cloud.firestore import AsyncClient
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import ModelHTTPError

from jarvis_core.assistant import list_models, memory_scope
from jarvis_core.auth import User, require_invoker, require_user
from jarvis_core.config import DeploymentConfig
from jarvis_core.dispatch import CloudTasksDispatcher
from jarvis_core.firestore_stores import FirestoreMemoryStore, FirestoreStepStore
from jarvis_core.secrets import SecretResolver
from jarvis_core.settings import AssistantSettings, EndpointConnection, FirestoreSettingsStore, SettingsConflictError
from jarvis_core.turns import QUEUED, SETTLED, FirestoreTurnStore, TurnConflict
from jarvis_core.worker import TurnWorker

_logger = logging.getLogger(__name__)

_ID_PATTERN = r'^[A-Za-z0-9_-]{1,128}$'
# A queued turn this old without progress is assumed to have lost its delivery task.
_QUEUED_GRACE = timedelta(minutes=10)
_RETRY_AFTER_SECONDS = '30'


class MessageIn(BaseModel):
    client_message_id: str = Field(pattern=_ID_PATTERN)
    text: str = Field(min_length=1, max_length=32_000)


class SettingsIn(BaseModel):
    expected_version: int | None
    settings: AssistantSettings


class MemoryWriteIn(BaseModel):
    content: str = Field(max_length=1_000_000)
    expected_version: str | None


_WEB_DIR = FilePath(__file__).parent / 'web'
# Largest memory file the vault view reads back in one request.
_MEMORY_READ_CHARS = 1_000_000


def create_app(config: DeploymentConfig | None = None) -> FastAPI:
    config = config or DeploymentConfig.from_env()
    client = AsyncClient(project=config.project_id)
    turns = FirestoreTurnStore(client)
    settings_store = FirestoreSettingsStore(client)
    secrets = SecretResolver()
    dispatcher = CloudTasksDispatcher(config)
    memory = FirestoreMemoryStore(client)
    worker = TurnWorker(
        turns=turns,
        settings=settings_store,
        # Media offload to Cloud Storage is added with the deployment slice; until then
        # snapshots keep media inline within the chunked size limit.
        steps=FirestoreStepStore(client, media_store=None),
        memory=memory,
        secrets=secrets,
        worker_id=config.worker_id,
    )
    app = FastAPI(title='JARVIS core', docs_url=None, redoc_url=None, openapi_url=None)

    async def current_user(request: Request) -> User:
        return await require_user(request, config)

    async def invoker(request: Request) -> None:
        await require_invoker(request, config)

    UserDep = Annotated[User, Depends(current_user)]
    IdPath = Annotated[str, Path(pattern=_ID_PATTERN)]

    @app.get('/health')
    async def health() -> dict[str, str]:
        """Process liveness only; it does not claim model, channel or tool health."""
        return {'status': 'alive'}

    @app.post('/v1/conversations/{conversation_id}/messages', status_code=202)
    async def post_message(conversation_id: IdPath, body: MessageIn, user: UserDep) -> dict[str, Any]:
        try:
            record, created = await turns.submit(
                uid=user.uid, conversation_id=conversation_id,
                client_message_id=body.client_message_id, text=body.text,
            )
        except TurnConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        dispatch = 'not_needed'
        if record['status'] == QUEUED:
            try:
                await dispatcher.dispatch(record['turn_id'])
                await turns.mark_dispatched(record['turn_id'])
                dispatch = 'queued'
            except GoogleAPICallError:
                _logger.exception('dispatch failed for turn %s; the repair sweep will retry', record['turn_id'])
                dispatch = 'deferred'
        return {'turn_id': record['turn_id'], 'status': record['status'], 'created': created, 'dispatch': dispatch}

    @app.get('/v1/settings')
    async def get_settings(user: UserDep) -> dict[str, Any]:
        stored = await settings_store.get(user.uid)
        if stored is None:
            raise HTTPException(status_code=404, detail='settings not configured')
        return stored.model_dump(mode='json')

    @app.put('/v1/settings')
    async def put_settings(body: SettingsIn, user: UserDep) -> dict[str, Any]:
        try:
            stored = await settings_store.save(user.uid, body.settings, expected_version=body.expected_version)
        except SettingsConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return stored.model_dump(mode='json')

    @app.post('/v1/connections/test', dependencies=[Depends(current_user)])
    async def test_connection(connection: EndpointConnection) -> dict[str, Any]:
        """List the endpoint's live catalog; report failures by type and HTTP status only."""
        try:
            models = await list_models(connection, secrets)
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the owner, none is hidden
            status = exc.status_code if isinstance(exc, ModelHTTPError) else getattr(exc, 'status_code', None)
            return {'ok': False, 'error': type(exc).__name__, 'http_status': status}
        return {'ok': True, 'models': models}

    @app.get('/config.json')
    async def web_config() -> dict[str, Any]:
        """Public client configuration for the PWA (Firebase web config is not a secret)."""
        return {'firebase': config.firebase_web_config}

    def _memory_path(user: User, path: str) -> str:
        full = f'{memory_scope(user.uid)}/{path}'
        try:
            validate_store_path(full)
        except ValueError:
            raise HTTPException(status_code=400, detail='invalid memory path') from None
        return full

    @app.get('/v1/memory')
    async def list_memory(user: UserDep) -> dict[str, list[str]]:
        prefix = f'{memory_scope(user.uid)}/'
        paths = await memory.list_paths(prefix, limit=1000)
        return {'paths': [path.removeprefix(prefix) for path in paths]}

    @app.get('/v1/memory/{path:path}')
    async def read_memory(path: str, user: UserDep) -> dict[str, Any]:
        file = await memory.read(_memory_path(user, path), max_chars=_MEMORY_READ_CHARS)
        if file is None:
            raise HTTPException(status_code=404, detail='memory file not found')
        return {'path': path, 'content': file.content, 'version': file.version, 'truncated': file.truncated}

    @app.put('/v1/memory/{path:path}')
    async def write_memory(path: str, body: MemoryWriteIn, user: UserDep) -> dict[str, Any]:
        """Owner correction of a memory file; a stale version is rejected, never overwritten."""
        try:
            result = await memory.write(_memory_path(user, path), body.content, expected_version=body.expected_version)
        except MemoryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {'path': path, 'version': result.version}

    @app.delete('/v1/memory/{path:path}')
    async def forget_memory(path: str, expected_version: str, user: UserDep) -> dict[str, Any]:
        try:
            result = await memory.delete(_memory_path(user, path), expected_version=expected_version)
        except MemoryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {'path': path, 'deleted': result.existed}

    @app.post('/internal/turns/{turn_id}/run', dependencies=[Depends(invoker)])
    async def run_turn(turn_id: IdPath, response: Response) -> dict[str, str]:
        if await turns.get(turn_id) is None:
            raise HTTPException(status_code=404, detail='unknown turn')
        outcome = await worker.run(turn_id)
        if outcome == 'not_runnable':
            turn = await turns.get(turn_id)
            if turn is not None and turn['status'] in SETTLED:
                return {'outcome': 'settled'}
        if outcome in ('not_runnable', 'requeued'):
            # Non-2xx makes Cloud Tasks retry this delivery with backoff.
            response.status_code = 503
            response.headers['Retry-After'] = _RETRY_AFTER_SECONDS
        return {'outcome': outcome}

    @app.post('/internal/repair', dependencies=[Depends(invoker)])
    async def repair() -> dict[str, int]:
        now = datetime.now(timezone.utc)
        stale = await turns.stale(dispatched_before=now - _QUEUED_GRACE)
        for turn in stale:
            await dispatcher.dispatch(turn['turn_id'], repair_of=(int(turn['attempt']), now))
            await turns.mark_dispatched(turn['turn_id'])
        return {'redispatched': len(stale)}

    # Mounted last so API routes take precedence over the PWA files.
    app.mount('/', StaticFiles(directory=_WEB_DIR, html=True), name='web')
    return app
