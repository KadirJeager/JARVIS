"""Exercise the real control routes and PC connector together, without cloud calls."""

from fastapi.testclient import TestClient

from app import harness_control, harness_tasks as ledger
from app.pc_connector import poll_once
from tests.fakes import FakeDB


class _Transaction:
    def get(self, ref):
        return iter((ref.get(),))

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


class _DB(FakeDB):
    def transaction(self):
        return _Transaction()


class _LocalHTTP:
    def __init__(self, api):
        self.api = api

    def request(self, method, url, *, headers, json_body=None, timeout=15):
        path = url.removeprefix("https://control.example")
        response = self.api.request(method, path, headers=headers, json=json_body)
        response.raise_for_status()
        return response.json()


def test_api_connector_claim_result_and_duplicate_event(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger.firestore, "transactional", lambda callback: callback)
    db = _DB()
    api = TestClient(harness_control.create_app(
        db=db, owner="kadir", worker_token="worker", ingest_token="ingest",
    ))
    body = {
        "source": "manual", "event_id": "one-event",
        "delivery_target": {"kind": "codex", "device": "pc"},
        "envelope": {"goal": "Read marker", "authority": "Read only",
                     "success_criteria": ["Marker matches"]},
    }
    first = api.post("/v1/tasks", json=body,
                     headers={"Authorization": "Bearer ingest"})
    assert first.status_code == 201
    marker = tmp_path / "marker.txt"
    marker.write_text("KONTROL-842", encoding="utf-8")
    calls = []

    def manager(task):
        calls.append(task["task_id"])
        assert marker.read_text(encoding="utf-8") == "KONTROL-842"
        return {"status": "completed", "result": {
            "summary": "Marker independently checked", "verified": True,
        }}

    options = dict(base_url="https://control.example", manager_kind="codex",
                   journal_path=tmp_path / "journal.sqlite3", run_manager=manager,
                   worker_token="worker", client=_LocalHTTP(api))
    assert poll_once(**options).launched == 1
    key = first.json()["task"]["task_id"]
    assert calls == [key]
    assert ledger.get(db, key)["status"] == ledger.COMPLETED
    duplicate = api.post("/v1/tasks", json=body,
                         headers={"Authorization": "Bearer ingest"})
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert poll_once(**options).launched == 0
    assert calls == [key]
