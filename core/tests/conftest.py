"""Run store tests against the official Firestore emulator.

The emulator ships as the `cloud-firestore-emulator` component of the Google
Cloud CLI and needs Java. It is started once per session on a free loopback
port; each test gets its own project id, which the emulator keeps isolated.
Set `FIRESTORE_EMULATOR_HOST` to reuse an emulator that is already running.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from google.cloud.firestore import AsyncClient


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _wait_until_ready(host: str, process: subprocess.Popen[bytes], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Firestore emulator exited with code {process.returncode}')
        try:
            with urllib.request.urlopen(f'http://{host}', timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f'Firestore emulator did not answer on {host} within {timeout}s')


@pytest.fixture(scope='session')
def firestore_emulator() -> Iterator[str]:
    existing = os.environ.get('FIRESTORE_EMULATOR_HOST')
    if existing:
        yield existing
        return
    gcloud = shutil.which('gcloud')
    if gcloud is None:
        raise RuntimeError('gcloud is required to start the Firestore emulator (component cloud-firestore-emulator)')
    host = f'127.0.0.1:{_free_port()}'
    process = subprocess.Popen(
        [gcloud, 'emulators', 'firestore', 'start', f'--host-port={host}', '--quiet'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_until_ready(host, process)
        os.environ['FIRESTORE_EMULATOR_HOST'] = host
        yield host
    finally:
        os.environ.pop('FIRESTORE_EMULATOR_HOST', None)
        # gcloud starts the emulator JVM as a child; stop the whole group.
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)


@pytest.fixture(scope='session')
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture(scope='session', autouse=True)
async def one_event_loop(anyio_backend: str) -> AsyncIterator[None]:
    """Keep one event loop for the whole session.

    anyio closes the loop after each test unless a wider async fixture holds
    it. gRPC channels opened by the Firestore clients deliver completions to
    the loop they started on; with a loop per test, a late completion hit an
    already closed loop and failed whichever test was running then.
    """
    yield


@pytest.fixture
def firestore_client(firestore_emulator: str) -> AsyncClient:
    return AsyncClient(project=f'test-{uuid4().hex[:12]}')
