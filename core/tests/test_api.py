"""HTTP surface of the core service on the real app and stores (Firestore emulator).

Two things are replaced, both outside this service: Google's token
verifiers (the tests hold no Google-signed tokens, so each test token maps to
the claims Google would have returned) and Cloud Tasks, whose deliveries are
recorded and then made by calling the worker endpoint the way Cloud Tasks
would. Everything behind them runs as deployed.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from google.api_core.exceptions import ServiceUnavailable
from google.cloud.firestore import AsyncClient

from jarvis_core import api as api_module
from jarvis_core import auth as auth_module
from jarvis_core.config import DeploymentConfig

pytestmark = pytest.mark.anyio

OWNER = 'owner@example.org'
SECOND = 'second@example.org'
SERVICE_URL = 'https://jarvis.example.org'
INVOKER = 'invoker@example-project.iam.gserviceaccount.com'
WEB_CONFIG = {'apiKey': 'public-key', 'authDomain': 'example.firebaseapp.com', 'projectId': 'p', 'appId': 'app'}

# Test token -> claims Google's verifier would return for it.
_PEOPLE: dict[str, dict[str, Any]] = {
    'owner': {'sub': 'uid-owner', 'email': OWNER, 'email_verified': True},
    'second': {'sub': 'uid-second', 'email': SECOND, 'email_verified': True},
    'unverified': {'sub': 'uid-x', 'email': OWNER, 'email_verified': False},
    'stranger': {'sub': 'uid-y', 'email': 'stranger@example.org', 'email_verified': True},
}
_SERVICES: dict[str, dict[str, Any]] = {
    'invoker': {'email': INVOKER, 'email_verified': True},
    'other-sa': {'email': 'other@example-project.iam.gserviceaccount.com', 'email_verified': True},
}


def _bearer(token: str) -> dict[str, str]:
    return {'authorization': f'Bearer {token}'}


class RecordingDispatcher:
    """Stands in for Cloud Tasks: records deliveries; `fail` makes the next dispatch fail like the API would."""

    instances: list[RecordingDispatcher] = []

    def __init__(self, config: DeploymentConfig) -> None:
        self.config = config
        self.dispatched: list[str] = []
        self.fail = False
        RecordingDispatcher.instances.append(self)

    async def dispatch(self, turn_id: str, *, repair_of: Any = None) -> None:
        if self.fail:
            self.fail = False
            raise ServiceUnavailable('queue unavailable')
        self.dispatched.append(turn_id)


@pytest.fixture
def config(firestore_emulator: str) -> DeploymentConfig:
    project = f'test-{uuid4().hex[:12]}'
    return DeploymentConfig(
        project_id=project,
        allowed_emails=frozenset({OWNER, SECOND}),
        service_url=SERVICE_URL,
        tasks_queue=f'projects/{project}/locations/europe-west1/queues/turns',
        invoker_service_account=INVOKER,
        worker_id='api-test',
        firebase_web_config=WEB_CONFIG,
    )


@pytest.fixture
async def client(
    config: DeploymentConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[httpx.AsyncClient]:
    def verify_person(token: str, _request: Any, audience: str) -> dict[str, Any]:
        assert audience == config.project_id
        if token not in _PEOPLE:
            raise ValueError('bad token')
        return _PEOPLE[token]

    def verify_service(token: str, _request: Any, audience: str) -> dict[str, Any]:
        if token not in _SERVICES or audience != SERVICE_URL:
            raise ValueError('bad token')
        return _SERVICES[token]

    monkeypatch.setattr(auth_module.id_token, 'verify_firebase_token', verify_person)
    monkeypatch.setattr(auth_module.id_token, 'verify_oauth2_token', verify_service)
    monkeypatch.setattr(api_module, 'CloudTasksDispatcher', RecordingDispatcher)
    (tmp_path / 'index.html').write_text('<main>shell</main>')
    monkeypatch.setattr(api_module, '_WEB_DIR', tmp_path)
    RecordingDispatcher.instances.clear()
    app = api_module.create_app(config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=SERVICE_URL) as http:
        yield http


@pytest.fixture
def dispatcher(client: httpx.AsyncClient) -> RecordingDispatcher:
    return RecordingDispatcher.instances[-1]


@pytest.fixture
def db(config: DeploymentConfig) -> AsyncClient:
    return AsyncClient(project=config.project_id)


async def _send(client: httpx.AsyncClient, conversation: str, message: str, text: str = 'hello') -> httpx.Response:
    return await client.post(
        f'/v1/conversations/{conversation}/messages',
        json={'client_message_id': message, 'text': text},
        headers=_bearer('owner'),
    )


# -- authentication -----------------------------------------------------------


async def test_liveness_and_public_config_need_no_token(client: httpx.AsyncClient) -> None:
    assert (await client.get('/health')).json() == {'status': 'alive'}
    assert (await client.get('/config.json')).json() == {'firebase': WEB_CONFIG}


@pytest.mark.parametrize(
    ('headers', 'status'),
    [
        ({}, 401),
        ({'authorization': 'Basic abc'}, 401),
        (_bearer('forged'), 401),
        (_bearer('unverified'), 403),
        (_bearer('stranger'), 403),
    ],
)
async def test_people_endpoints_reject_missing_invalid_and_unlisted_accounts(
    client: httpx.AsyncClient, headers: dict[str, str], status: int
) -> None:
    assert (await client.get('/v1/me', headers=headers)).status_code == status


async def test_allowed_account_is_identified_by_its_firebase_uid(client: httpx.AsyncClient) -> None:
    response = await client.get('/v1/me', headers=_bearer('owner'))
    assert response.json() == {'uid': 'uid-owner', 'email': OWNER}


@pytest.mark.parametrize(('token', 'status'), [(None, 401), ('owner', 401), ('other-sa', 403)])
async def test_internal_endpoints_accept_only_the_invoker_account(
    client: httpx.AsyncClient, token: str | None, status: int
) -> None:
    headers = _bearer(token) if token else {}
    assert (await client.post('/internal/repair', headers=headers)).status_code == status


async def test_unknown_api_paths_are_not_the_app_shell(client: httpx.AsyncClient) -> None:
    assert (await client.get('/v1/nothing-here', headers=_bearer('owner'))).status_code == 404
    assert (await client.get('/c/some-conversation')).text == '<main>shell</main>'


# -- messages and turns -------------------------------------------------------


async def test_message_is_queued_once_and_dispatched(
    client: httpx.AsyncClient, dispatcher: RecordingDispatcher
) -> None:
    first = await _send(client, 'c1', 'm1')
    assert first.status_code == 202
    body = first.json()
    assert body['status'] == 'queued' and body['created'] is True and body['dispatch'] == 'queued'
    again = (await _send(client, 'c1', 'm1')).json()
    assert again['turn_id'] == body['turn_id'] and again['created'] is False
    # A resubmitted message may re-dispatch; Cloud Tasks collapses it by task name.
    assert set(dispatcher.dispatched) == {body['turn_id']}


async def test_same_message_id_with_different_text_conflicts(client: httpx.AsyncClient) -> None:
    await _send(client, 'c1', 'm1', 'first')
    assert (await _send(client, 'c1', 'm1', 'changed')).status_code == 409


async def test_failed_dispatch_is_accepted_and_left_to_the_repair_sweep(
    client: httpx.AsyncClient, dispatcher: RecordingDispatcher
) -> None:
    dispatcher.fail = True
    body = (await _send(client, 'c1', 'm1')).json()
    assert body['status'] == 'queued' and body['dispatch'] == 'deferred'
    assert dispatcher.dispatched == []


@pytest.mark.parametrize(
    'payload',
    [
        {'client_message_id': 'bad id!', 'text': 'x'},
        {'client_message_id': 'm1', 'text': ''},
        {'client_message_id': 'm1', 'text': 'x' * 32_001},
    ],
)
async def test_message_input_is_validated(client: httpx.AsyncClient, payload: dict[str, str]) -> None:
    response = await client.post('/v1/conversations/c1/messages', json=payload, headers=_bearer('owner'))
    assert response.status_code == 422


async def test_turn_without_settings_fails_visibly_through_the_worker_endpoint(
    client: httpx.AsyncClient, dispatcher: RecordingDispatcher, db: AsyncClient
) -> None:
    turn_id = (await _send(client, 'c1', 'm1')).json()['turn_id']
    response = await client.post(f'/internal/turns/{dispatcher.dispatched[0]}/run', headers=_bearer('invoker'))
    assert response.status_code == 200 and response.json() == {'outcome': 'failed'}
    turn = (await db.collection('turns').document(turn_id).get()).to_dict()
    assert turn is not None and turn['status'] == 'failed' and turn['error']['type'] == 'SettingsMissing'
    # Settled: a late redelivery is acknowledged, not retried.
    again = await client.post(f'/internal/turns/{turn_id}/run', headers=_bearer('invoker'))
    assert again.status_code == 200 and again.json() == {'outcome': 'settled'}


async def test_worker_endpoint_rejects_unknown_turns(client: httpx.AsyncClient) -> None:
    response = await client.post('/internal/turns/no-such-turn/run', headers=_bearer('invoker'))
    assert response.status_code == 404


async def test_owner_cancels_a_queued_turn_and_others_cannot_see_it(client: httpx.AsyncClient) -> None:
    turn_id = (await _send(client, 'c1', 'm1')).json()['turn_id']
    assert (await client.post(f'/v1/turns/{turn_id}/cancel', headers=_bearer('second'))).status_code == 404
    response = await client.post(f'/v1/turns/{turn_id}/cancel', headers=_bearer('owner'))
    assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    # Cancelling again is idempotent.
    again = await client.post(f'/v1/turns/{turn_id}/cancel', headers=_bearer('owner'))
    assert again.status_code == 200 and again.json()['status'] == 'cancelled'


async def test_a_failed_turn_cannot_be_cancelled(client: httpx.AsyncClient, dispatcher: RecordingDispatcher) -> None:
    turn_id = (await _send(client, 'c1', 'm1')).json()['turn_id']
    await client.post(f'/internal/turns/{turn_id}/run', headers=_bearer('invoker'))
    assert (await client.post(f'/v1/turns/{turn_id}/cancel', headers=_bearer('owner'))).status_code == 409


# -- conversations --------------------------------------------------------------


async def test_conversation_rename_pin_and_delete(client: httpx.AsyncClient, db: AsyncClient) -> None:
    turn_id = (await _send(client, 'c1', 'm1')).json()['turn_id']
    assert (await client.patch('/v1/conversations/missing', json={'title': 'x'}, headers=_bearer('owner'))).status_code == 404
    patched = await client.patch('/v1/conversations/c1', json={'title': 'Plan', 'pinned': True}, headers=_bearer('owner'))
    assert patched.status_code == 200
    head = (await db.document('users/uid-owner/conversations/c1').get()).to_dict()
    assert head is not None and head['title'] == 'Plan' and head['pinned'] is True

    # A queued turn keeps the conversation; once it settles the conversation can go.
    assert (await client.delete('/v1/conversations/c1', headers=_bearer('owner'))).status_code == 409
    await client.post(f'/v1/turns/{turn_id}/cancel', headers=_bearer('owner'))
    deleted = await client.delete('/v1/conversations/c1', headers=_bearer('owner'))
    assert deleted.status_code == 200 and deleted.json()['conversation_id'] == 'c1'
    assert not (await db.document('users/uid-owner/conversations/c1').get()).exists
    assert (await client.delete('/v1/conversations/c1', headers=_bearer('owner'))).status_code == 404


# -- settings -------------------------------------------------------------------


_SETTINGS = {'model': {'protocol': 'openai', 'base_url': 'http://localhost:8317/v1', 'model': 'some-model'}}


async def test_settings_are_versioned_and_stale_writes_conflict(client: httpx.AsyncClient) -> None:
    assert (await client.get('/v1/settings', headers=_bearer('owner'))).status_code == 404
    saved = await client.put('/v1/settings', json={'expected_version': None, 'settings': _SETTINGS}, headers=_bearer('owner'))
    assert saved.status_code == 200 and saved.json()['version'] == 1
    stale = await client.put('/v1/settings', json={'expected_version': None, 'settings': _SETTINGS}, headers=_bearer('owner'))
    assert stale.status_code == 409
    updated = await client.put('/v1/settings', json={'expected_version': 1, 'settings': _SETTINGS}, headers=_bearer('owner'))
    assert updated.json()['version'] == 2
    current = (await client.get('/v1/settings', headers=_bearer('owner'))).json()
    assert current['version'] == 2 and current['settings']['model']['model'] == 'some-model'
    # Settings belong to the account that saved them.
    assert (await client.get('/v1/settings', headers=_bearer('second'))).status_code == 404


async def test_settings_reject_plain_http_to_non_loopback_hosts(client: httpx.AsyncClient) -> None:
    unsafe = {'model': {'protocol': 'openai', 'base_url': 'http://169.254.169.254/v1', 'model': 'm'}}
    response = await client.put('/v1/settings', json={'expected_version': None, 'settings': unsafe}, headers=_bearer('owner'))
    assert response.status_code == 422


async def test_connection_test_reports_failures_without_details(client: httpx.AsyncClient) -> None:
    # Nothing listens on this loopback port: the failure comes back as data, not as a 500.
    connection = {'protocol': 'openai', 'base_url': 'http://127.0.0.1:9/v1'}
    response = await client.post('/v1/connections/test', json=connection, headers=_bearer('owner'))
    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is False and set(body) == {'ok', 'error', 'http_status'}


# -- memory (vault) ---------------------------------------------------------------


async def test_memory_write_read_search_export_and_forget(client: httpx.AsyncClient) -> None:
    owner = _bearer('owner')
    written = await client.put('/v1/memory/notes/plan.md', json={'content': 'buy oat milk', 'expected_version': None}, headers=owner)
    assert written.status_code == 200
    version = written.json()['version']
    stale = await client.put('/v1/memory/notes/plan.md', json={'content': 'other', 'expected_version': None}, headers=owner)
    assert stale.status_code == 409

    read = (await client.get('/v1/memory/notes/plan.md', headers=owner)).json()
    assert read['content'] == 'buy oat milk' and read['version'] == version
    listing = (await client.get('/v1/memory', headers=owner)).json()
    assert [file['path'] for file in listing['files']] == ['notes/plan.md']
    matches = (await client.get('/v1/memory-search', params={'q': 'oat'}, headers=owner)).json()['matches']
    assert [match['path'] for match in matches] == ['notes/plan.md']

    export = await client.get('/v1/memory-export', headers=owner)
    assert export.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert archive.read('notes/plan.md').decode() == 'buy oat milk'

    # Another account sees none of it.
    assert (await client.get('/v1/memory', headers=_bearer('second'))).json() == {'files': []}
    assert (await client.get('/v1/memory/notes/plan.md', headers=_bearer('second'))).status_code == 404

    gone = await client.delete('/v1/memory/notes/plan.md', params={'expected_version': version}, headers=owner)
    assert gone.status_code == 200 and gone.json()['deleted'] is True
    assert (await client.get('/v1/memory/notes/plan.md', headers=owner)).status_code == 404


async def test_memory_paths_cannot_leave_the_owner_scope(client: httpx.AsyncClient) -> None:
    response = await client.get('/v1/memory/../uid-second/MEMORY.md', headers=_bearer('owner'))
    assert response.status_code in (400, 404)
    response = await client.get('/v1/memory/notes/%2E%2E/%2E%2E/x.md', headers=_bearer('owner'))
    assert response.status_code == 400


# -- optional features ----------------------------------------------------------


async def test_push_and_key_storage_report_themselves_as_not_configured(client: httpx.AsyncClient) -> None:
    owner = _bearer('owner')
    assert (await client.get('/v1/push', headers=owner)).json() == {'configured': False}
    subscription = {'endpoint': 'https://push.example.org/x', 'keys': {'p256dh': 'a', 'auth': 'b'}}
    assert (await client.put('/v1/push/subscriptions', json=subscription, headers=owner)).status_code == 404
    assert (await client.put('/v1/secrets/model-key', json={'value': 'v'}, headers=owner)).status_code == 404


async def test_status_reads_installation_facts(client: httpx.AsyncClient) -> None:
    await client.put('/v1/settings', json={'expected_version': None, 'settings': _SETTINGS}, headers=_bearer('owner'))
    status = (await client.get('/v1/status', headers=_bearer('owner'))).json()
    assert status['service']['service_url'] == SERVICE_URL
    assert status['account'] == {'email': OWNER}
    assert status['settings']['version'] == 1 and status['settings']['model'] == 'some-model'
    assert status['push'] == {'configured': False}
    assert status['memory']['files'] == 0
