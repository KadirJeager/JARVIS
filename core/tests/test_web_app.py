"""Serving the built PWA: client routes get the app shell, real misses stay 404."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis_core.api import WebApp


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text('<main>shell</main>')
    (tmp_path / 'sw.js').write_text('// worker')
    (tmp_path / 'assets' / 'index-abc123.js').write_text('// app')
    app = FastAPI()
    app.mount('/', WebApp(directory=tmp_path, html=True), name='web')
    return TestClient(app)


@pytest.mark.parametrize('path', ['/', '/c/conv-1', '/brain', '/share?text=hello'])
def test_app_routes_get_the_shell_revalidated(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.text == '<main>shell</main>'
    assert response.headers['cache-control'] == 'no-cache'


def test_hashed_assets_are_immutable(client: TestClient) -> None:
    response = client.get('/assets/index-abc123.js')
    assert response.status_code == 200
    assert 'immutable' in response.headers['cache-control']


def test_service_worker_is_revalidated(client: TestClient) -> None:
    response = client.get('/sw.js')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-cache'


@pytest.mark.parametrize('path', ['/assets/gone.js', '/icon-9.png', '/v1/unknown', '/internal/unknown'])
def test_real_misses_stay_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
