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

import io
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path as FilePath
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import GoogleAPICallError, NotFound, PermissionDenied
from pydantic_ai_harness.memory import MemoryConflictError
from pydantic_ai_harness.memory._store import validate_store_path  # pyright: ignore[reportPrivateUsage]
from google.cloud.firestore import AsyncClient
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import ModelHTTPError

from jarvis_core.assistant import MAIN_MEMORY_FILE, capability_manifest, list_models, memory_capability, memory_scope
from jarvis_core.auth import User, require_invoker, require_user
from jarvis_core.config import DeploymentConfig
from jarvis_core.dispatch import CloudTasksDispatcher
from jarvis_core.firestore_stores import FirestoreMemoryStore, FirestoreStepStore
from jarvis_core.notify import PushNotifier, PushSubscriptionIn
from jarvis_core.secrets import SecretCatalog, SecretResolver
from jarvis_core.settings import AssistantSettings, EndpointConnection, FirestoreSettingsStore, SettingsConflictError
from jarvis_core.turns import (
    QUEUED,
    SETTLED,
    ConversationBusy,
    FirestoreTurnStore,
    InvalidTransition,
    TurnConflict,
)
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


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=64_000)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    pinned: bool | None = None


_WEB_DIR = FilePath(__file__).parent / 'web'
# Largest memory file the vault view reads back in one request.
_MEMORY_READ_CHARS = 1_000_000
# Upper bound on files one vault listing, search or export covers.
_MEMORY_FILES_LIMIT = 1000


def create_app(config: DeploymentConfig | None = None) -> FastAPI:
    config = config or DeploymentConfig.from_env()
    client = AsyncClient(project=config.project_id)
    turns = FirestoreTurnStore(client)
    settings_store = FirestoreSettingsStore(client)
    secrets = SecretResolver()
    catalog = SecretCatalog(config.project_id, config.secret_prefix or '')
    dispatcher = CloudTasksDispatcher(config)
    memory = FirestoreMemoryStore(client)
    # Media offload to Cloud Storage comes with attachments; until then snapshots keep
    # media inline within the chunked size limit.
    steps = FirestoreStepStore(client, media_store=None)
    notifier = (
        PushNotifier(client, secrets, key_ref=config.vapid_key_ref, subject=config.service_url)
        if config.vapid_key_ref is not None
        else None
    )
    worker = TurnWorker(
        turns=turns,
        settings=settings_store,
        steps=steps,
        memory=memory,
        secrets=secrets,
        worker_id=config.worker_id,
        notifier=notifier,
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

    @app.get('/v1/me')
    async def me(user: UserDep) -> dict[str, str]:
        """The signed-in account, if this installation allows it (403 otherwise)."""
        return {'uid': user.uid, 'email': user.email}

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

    @app.get('/v1/secrets')
    async def list_secrets(user: UserDep) -> dict[str, Any]:
        """Secret names and references for the settings picker; values are never returned."""
        try:
            items = await catalog.list()
        except PermissionDenied:
            raise HTTPException(status_code=403, detail='the service may not list secrets') from None
        return {'items': items, 'managed_prefix': config.secret_prefix, 'can_store': config.secret_prefix is not None}

    @app.put('/v1/secrets/{slug}')
    async def store_secret(slug: IdPath, body: SecretIn, user: UserDep) -> dict[str, str]:
        """Store a key the owner pasted as the newest version of a managed secret; return its reference."""
        if config.secret_prefix is None:
            raise HTTPException(status_code=404, detail='storing secrets is not configured')
        try:
            ref = await catalog.put(slug, body.value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except PermissionDenied:
            raise HTTPException(status_code=403, detail='the service may not store this secret') from None
        secrets.forget(ref)
        return {'ref': ref}

    @app.delete('/v1/secrets/{slug}')
    async def delete_secret(slug: IdPath, user: UserDep) -> dict[str, bool]:
        if config.secret_prefix is None:
            raise HTTPException(status_code=404, detail='storing secrets is not configured')
        try:
            await catalog.delete(slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except NotFound:
            raise HTTPException(status_code=404, detail='secret not found') from None
        except PermissionDenied:
            raise HTTPException(status_code=403, detail='the service may not delete this secret') from None
        return {'deleted': True}

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
    async def list_memory(user: UserDep) -> dict[str, Any]:
        prefix = f'{memory_scope(user.uid)}/'
        files = await memory.list_files(prefix, limit=_MEMORY_FILES_LIMIT)
        return {'files': [{**file, 'path': file['path'].removeprefix(prefix)} for file in files]}

    @app.get('/v1/memory-search')
    async def search_memory(user: UserDep, q: Annotated[str, Query(min_length=1, max_length=500)]) -> dict[str, Any]:
        """The same lexical search the assistant's `search_memory` tool uses, over the owner's vault."""
        prefix = f'{memory_scope(user.uid)}/'
        result = await memory.search(
            prefix, q, limit=50, max_files=_MEMORY_FILES_LIMIT, max_chars=20_000, max_file_chars=_MEMORY_READ_CHARS
        )
        return {
            'matches': [
                {'path': match.path.removeprefix(prefix), 'snippet': match.snippet, 'score': match.score}
                for match in result.matches
            ],
            'scanned': result.scanned,
            'truncated': result.truncated,
        }

    @app.get('/v1/memory-export')
    async def export_memory(user: UserDep) -> Response:
        """Every vault file in a zip archive with the vault's own paths."""
        prefix = f'{memory_scope(user.uid)}/'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path, content in await memory.read_all(prefix, limit=_MEMORY_FILES_LIMIT):
                archive.writestr(path.removeprefix(prefix), content)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        return Response(
            buffer.getvalue(),
            media_type='application/zip',
            headers={'Content-Disposition': f'attachment; filename="jarvis-hafiza-{stamp}.zip"'},
        )

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

    @app.patch('/v1/conversations/{conversation_id}')
    async def update_conversation(conversation_id: IdPath, body: ConversationPatch, user: UserDep) -> dict[str, Any]:
        try:
            await turns.update_conversation(user.uid, conversation_id, title=body.title, pinned=body.pinned)
        except KeyError:
            raise HTTPException(status_code=404, detail='conversation not found') from None
        return {'conversation_id': conversation_id}

    @app.delete('/v1/conversations/{conversation_id}')
    async def delete_conversation(conversation_id: IdPath, user: UserDep) -> dict[str, Any]:
        """Forget a conversation: its messages, turns and the model-facing history of every attempt."""
        try:
            run_ids = await turns.delete_conversation(user.uid, conversation_id)
        except KeyError:
            raise HTTPException(status_code=404, detail='conversation not found') from None
        except ConversationBusy:
            raise HTTPException(status_code=409, detail='conversation has a queued or running turn') from None
        for run_id in run_ids:
            await steps.delete_run(run_id=run_id)
        return {'conversation_id': conversation_id, 'deleted_runs': len(run_ids)}

    @app.post('/v1/turns/{turn_id}/cancel')
    async def cancel_turn(turn_id: IdPath, user: UserDep) -> dict[str, Any]:
        try:
            turn = await turns.request_cancel(turn_id, uid=user.uid)
        except KeyError:
            raise HTTPException(status_code=404, detail='turn not found') from None
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {'turn_id': turn_id, 'status': turn['status']}

    @app.post('/v1/turns/{turn_id}/close')
    async def close_turn(turn_id: IdPath, user: UserDep) -> dict[str, Any]:
        """The owner checked a turn awaiting reconciliation and closes it."""
        try:
            turn = await turns.close_reconciliation(turn_id, uid=user.uid)
        except KeyError:
            raise HTTPException(status_code=404, detail='turn not found') from None
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {'turn_id': turn_id, 'status': turn['status']}

    def _push() -> PushNotifier:
        if notifier is None:
            raise HTTPException(status_code=404, detail='push notifications are not configured')
        return notifier

    @app.get('/v1/push')
    async def push_status(user: UserDep) -> dict[str, Any]:
        return await notifier.status(user.uid) if notifier is not None else {'configured': False}

    @app.put('/v1/push/subscriptions')
    async def push_subscribe(body: PushSubscriptionIn, request: Request, user: UserDep) -> dict[str, str]:
        key = await _push().subscribe(user.uid, body, user_agent=request.headers.get('user-agent', ''))
        return {'id': key}

    @app.delete('/v1/push/subscriptions/{subscription_id}')
    async def push_unsubscribe(subscription_id: IdPath, user: UserDep) -> dict[str, bool]:
        return {'deleted': await _push().unsubscribe(user.uid, subscription_id)}

    @app.post('/v1/push/test')
    async def push_test(
        user: UserDep, subscription_id: Annotated[str | None, Query(pattern=_ID_PATTERN)] = None
    ) -> dict[str, Any]:
        """Send a real test message; the result is what each push service answered."""
        outcomes = await _push().send(user.uid, {'kind': 'test'}, only=subscription_id)
        return {'outcomes': outcomes}

    @app.get('/v1/status')
    async def status(user: UserDep) -> dict[str, Any]:
        """Installation facts the panel shows; each value is read, none is assumed."""
        stored = await settings_store.get(user.uid)
        prefix = f'{memory_scope(user.uid)}/'
        files = await memory.list_files(prefix, limit=_MEMORY_FILES_LIMIT)
        main = await memory.read(f'{prefix}{MAIN_MEMORY_FILE}', max_chars=_MEMORY_READ_CHARS)
        injection = memory_capability(memory, user.uid)
        return {
            'service': {
                'revision': os.environ.get('K_REVISION'),
                'build': os.environ.get('JARVIS_BUILD'),
                'project_id': config.project_id,
                'service_url': config.service_url,
            },
            'account': {'email': user.email},
            'settings': None if stored is None else {
                'version': stored.version,
                'updated_at': stored.updated_at,
                'protocol': stored.settings.model.protocol,
                'base_url': stored.settings.model.base_url,
                'model': stored.settings.model.model,
                'tool_selection': stored.settings.tool_selection.strategy,
            },
            'memory': {
                'files': len(files),
                'total_chars': sum(int(file['chars']) for file in files),
                'main_file': MAIN_MEMORY_FILE,
                'main_chars': None if main is None else len(main.content),
                'main_lines': None if main is None else len(main.content.splitlines()),
                'injection_max_tokens': injection.max_tokens,
                'injection_max_lines': injection.max_lines,
            },
            'push': await notifier.status(user.uid) if notifier is not None else {'configured': False},
            'capabilities': capability_manifest(None if stored is None else stored.settings),
        }

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
