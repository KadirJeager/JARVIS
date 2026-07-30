"""POST /api/jobs/repo-watch — Cloud Scheduler OIDC koruması + poller tetikleme.

Gerçek ağ yok: id_token.verify_oauth2_token ve repo_watch.poll_once
monkeypatch'lenir (test_api.py deseni).
"""
import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.main as main_mod
from app import config, repo_watch
from app.memory import Memory
from tests.fakes import FakeDB

SA = "repo-watch-scheduler@proj.iam.gserviceaccount.com"
AUD = "https://jarvis-brain-xyz.run.app"
SUMMARY = {"kontrol": 2, "olay": 1, "hata": 0}


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(config, "SCHEDULER_SA", SA)
    monkeypatch.setattr(config, "SCHEDULER_AUD", AUD)


def _post(c, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post("/api/jobs/repo-watch", headers=headers)


def test_job_without_token_returns_401(configured):
    with TestClient(main_mod.app) as c:
        assert _post(c).status_code == 401


def test_job_invalid_token_returns_401(configured, monkeypatch):
    def fake_verify(token, request, audience):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        assert _post(c, "bogus").status_code == 401


def test_job_wrong_service_account_returns_403(configured, monkeypatch):
    def fake_verify(token, request, audience):
        return {"email": "attacker@other.iam.gserviceaccount.com"}

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        assert _post(c, "sometoken").status_code == 403


def test_job_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(config, "SCHEDULER_SA", "")
    monkeypatch.setattr(config, "SCHEDULER_AUD", "")
    with TestClient(main_mod.app) as c:
        assert _post(c, "sometoken").status_code == 503


def test_job_correct_token_runs_poller_and_returns_summary(configured, monkeypatch):
    def fake_verify(token, request, audience):
        assert audience == AUD
        return {"email": SA}

    calls = []

    def fake_poll(db, client):
        calls.append(db)
        return SUMMARY

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(repo_watch, "poll_once", fake_poll)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(FakeDB()))

    with TestClient(main_mod.app) as c:
        r = _post(c, "good-token")

    assert r.status_code == 200
    assert r.json() == SUMMARY
    assert len(calls) == 1
