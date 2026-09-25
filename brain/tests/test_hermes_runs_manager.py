"""Hermes Runs contract using an in-memory HTTP double; no model or gateway."""

import json
import sqlite3
from urllib.parse import urlsplit

import pytest

from app.hermes_runs_manager import make_hermes_runs_manager
from app.pc_connector import ConnectorError, LaunchJournal, PollReport, main, poll_once


def task(key="t1"):
    return {"task_id": key, "status": "queued",
            "delivery_target": {"device": "pc", "kind": "hermes"},
            "envelope": {"goal": "Read a marker", "authority": "read only",
                         "success_criteria": ["Marker matched"]}}


def output(status="completed", *, evidence="passed"):
    return json.dumps({"status": status, "summary": "Marker matched",
                       "tools_used": ["terminal"],
                       "evidence": [{"what": "marker", "check": "exact",
                                     "outcome": evidence}], "next_action": ""})


class FakeHermes:
    def __init__(self, statuses=None):
        self.statuses = list(statuses or [{"status": "completed", "output": output()}])
        self.posts = []
        self.gets = 0

    def request(self, method, url, *, headers, json_body=None, timeout=15):
        assert urlsplit(url).netloc == "127.0.0.1:8642"
        assert headers["Authorization"] == "Bearer test-local-key"
        path = urlsplit(url).path
        if method == "GET" and path == "/v1/capabilities":
            return {"features": {"runs_idempotency": {
                "supported": True, "durable": True, "retention_seconds": 86400}}}
        if method == "POST" and path == "/v1/runs":
            assert headers["Idempotency-Key"].startswith("jarvis-task-")
            assert "Read a marker" in json_body["input"]
            self.posts.append((headers["Idempotency-Key"], json_body))
            return {"run_id": "run_local1", "status": "started"}
        if method == "GET" and path == "/v1/runs/run_local1":
            index = min(self.gets, len(self.statuses) - 1)
            self.gets += 1
            return {"run_id": "run_local1", "session_id": "jarvis-session1",
                    **self.statuses[index]}
        raise AssertionError((method, path))


def manager(client, **kwargs):
    return make_hermes_runs_manager(client=client, api_key="test-local-key",
                                    poll_interval=0.001, **kwargs)


def test_submits_with_stable_key_polls_and_requires_independent_proof():
    fake = FakeHermes([{"status": "running"}, {"status": "completed", "output": output()}])
    observed = []
    run = manager(fake, verify=lambda task, status, report:
                  observed.append((task["task_id"], status["run_id"], report["summary"])) or True)
    result = run(task())
    assert result["status"] == "completed"
    assert result["session_id"] == "jarvis-session1"
    assert result["result"]["hermes_run_id"] == "run_local1"
    assert observed == [("t1", "run_local1", "Marker matched")]
    assert fake.gets == 2
    run(task())
    assert fake.posts[0] == fake.posts[1]


def test_unverified_or_unproven_completed_run_fails_closed():
    fake = FakeHermes()
    assert manager(fake)(task())["result"]["reason"] == "verification_failed"
    assert manager(fake, verify=lambda *_: False)(task())["status"] == "failed"
    fake = FakeHermes([{"status": "completed", "output": output(evidence="unknown")}])
    assert manager(fake, verify=lambda *_: True)(task())["status"] == "failed"


def test_trusted_open_ended_policy_records_agent_report_assurance():
    fake = FakeHermes()
    outcome = manager(fake, allow_agent_report=True)(task())
    assert outcome["status"] == "completed"
    assert outcome["result"]["verification"] == "agent_reported"
    assert manager(fake, allow_agent_report=True, verify=lambda *_: False)(task())["status"] == "failed"
    weak = FakeHermes([{"status": "completed", "output": output(evidence="unknown")}])
    assert manager(weak, allow_agent_report=True)(task())["status"] == "failed"


def test_bad_output_and_failed_run_do_not_claim_success():
    fake = FakeHermes([{"status": "completed", "output": "not json"}])
    assert manager(fake, verify=lambda *_: True)(task())["result"] == {
        "reason": "invalid_manager_report", "hermes_run_id": "run_local1"}
    fake = FakeHermes([{"status": "interrupted", "error": "secret material"}])
    result = manager(fake)(task())
    assert result["result"] == {"reason": "hermes_run_interrupted",
                                "hermes_run_id": "run_local1"}
    assert "secret" not in str(result)


def test_pending_approval_and_timeout_are_bounded():
    fake = FakeHermes([{"status": "waiting_for_approval"}])
    assert manager(fake)(task())["status"] == "waiting_user"
    fake = FakeHermes([{"status": "running"}])
    clock = iter([0, 0, 1])
    with pytest.raises(ConnectorError, match="did not finish"):
        manager(fake, max_wait_seconds=0.5, monotonic=lambda: next(clock),
                sleep=lambda _: None)(task())
    assert fake.gets == 2


def test_rejects_remote_url_and_mismatched_status_identity():
    with pytest.raises(ValueError, match="local HTTP"):
        make_hermes_runs_manager(base_url="https://other.example", api_key="x")
    fake = FakeHermes()
    original = fake.request

    def bad_request(*args, **kwargs):
        response = original(*args, **kwargs)
        if args[0] == "GET":
            response["run_id"] = "run_wrong"
        return response

    fake.request = bad_request
    with pytest.raises(ConnectorError, match="mismatched run ID"):
        manager(fake)(task())


def test_refuses_non_durable_hermes_before_submission():
    fake = FakeHermes()
    original = fake.request

    def nondurable(*args, **kwargs):
        if urlsplit(args[1]).path == "/v1/capabilities":
            return {"features": {"runs_idempotency": {
                "supported": True, "durable": False, "retention_seconds": 86400}}}
        return original(*args, **kwargs)

    fake.request = nondurable
    with pytest.raises(ConnectorError, match="durable run idempotency"):
        manager(fake)(task())
    assert fake.posts == []


def test_manager_is_compatible_with_pc_connector_callback(tmp_path):
    class Control:
        def __init__(self):
            self.item = task()
            self.posts = []

        def request(self, method, url, *, headers, json_body=None, timeout=15):
            path = urlsplit(url).path
            if method == "GET" and path == "/v1/tasks/queued":
                return {"tasks": [self.item] if self.item["status"] == "queued" else []}
            if method == "POST" and path.endswith("/claim"):
                self.item["status"] = "claimed"
                return {"claimed": True, "task": self.item.copy()}
            if method == "POST" and path.endswith("/outcome"):
                self.posts.append(json_body)
                self.item["status"] = json_body["status"]
                return {"task": self.item.copy()}
            raise AssertionError((method, path))

    control = Control()
    report = poll_once(base_url="https://control.example", manager_kind="hermes",
                       journal_path=tmp_path / "journal.sqlite3", client=control,
                       worker_token="worker-test", run_manager=manager(
                           FakeHermes(), verify=lambda *_: True))
    assert report.launched == 1
    assert [item["status"] for item in control.posts] == ["running", "completed"]
    assert control.posts[-1]["session_id"] == "jarvis-session1"
    assert control.posts[-1]["run_id"] == "t1"
    assert control.posts[-1]["result"]["hermes_run_id"] == "run_local1"


def test_main_selects_hermes_runs_driver_without_terminal_argv(tmp_path, monkeypatch, capsys):
    config = tmp_path / "worker.json"
    config.write_text(json.dumps({"driver": "hermes_runs", "manager_kind": "hermes",
                                  "hermes_url": "http://127.0.0.1:8642"}))
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "test-local-key")
    seen = []

    def fake_poll(**kwargs):
        seen.append(kwargs)
        return PollReport()

    monkeypatch.setattr("app.pc_connector.poll_once", fake_poll)
    assert main(["--config", str(config), "--base-url", "https://control.example",
                 "--journal", str(tmp_path / "journal.sqlite3")]) == 0
    assert seen[0]["manager_kind"] == "hermes"
    assert seen[0]["reconcile_manager"] is seen[0]["run_manager"]
    assert callable(seen[0]["run_manager"].replay_fingerprint)
    assert '"launched":0' in capsys.readouterr().out


def test_crashed_hermes_run_reconciles_same_idempotent_submission(tmp_path):
    class Control:
        def __init__(self):
            self.item = task()
            self.terminal = []

        def request(self, method, url, *, headers, json_body=None, timeout=15):
            path = urlsplit(url).path
            if method == "GET" and path == "/v1/tasks/queued":
                return {"tasks": [self.item.copy()] if self.item["status"] == "queued" else []}
            if method == "GET" and path == "/v1/tasks/t1":
                return {"task": self.item.copy()}
            if method == "POST" and path.endswith("/claim"):
                self.item["status"] = "claimed"
                return {"claimed": True, "task": self.item.copy()}
            if method == "POST" and path.endswith("/outcome"):
                self.item["status"] = json_body["status"]
                if json_body["status"] != "running":
                    self.terminal.append(json_body)
                return {"task": self.item.copy()}
            raise AssertionError((method, path))

    control = Control()
    fake = FakeHermes([{"status": "running"}])
    clock = iter([0, 0, 1])
    first = manager(fake, max_wait_seconds=0.5,
                    monotonic=lambda: next(clock), sleep=lambda _: None)
    journal = tmp_path / "journal.sqlite3"
    common = {"base_url": "https://control.example", "manager_kind": "hermes",
              "journal_path": journal, "worker_token": "worker-test", "client": control}
    assert poll_once(**common, run_manager=first, reconcile_manager=first).uncertain == 1
    assert LaunchJournal(journal).state("t1") == "launched"
    assert len(fake.posts) == 1

    fake.statuses = [{"status": "completed", "output": output()}]
    fake.gets = 0
    second = manager(fake, verify=lambda *_: True)
    report = poll_once(**common, run_manager=second, reconcile_manager=second)
    assert report.replayed == 1
    assert report.launched == 0
    assert len(fake.posts) == 2
    assert fake.posts[0] == fake.posts[1]
    assert control.terminal[0]["status"] == "completed"
    assert LaunchJournal(journal).state("t1") is None


def test_old_or_changed_replay_identity_stays_uncertain(tmp_path):
    from tests.test_pc_connector import FakeControl

    control = FakeControl([task()])
    control.tasks["t1"]["status"] = "running"
    journal_path = tmp_path / "journal.sqlite3"
    journal = LaunchJournal(journal_path)
    journal.prepare("t1")
    journal.ready("t1")
    journal.launched("t1", replay_fingerprint="previous-credential-or-body")
    run = manager(FakeHermes())
    common = {"base_url": "https://control.example", "manager_kind": "hermes",
              "journal_path": journal_path, "worker_token": "synthetic-token",
              "client": control, "run_manager": run, "reconcile_manager": run}
    assert poll_once(**common).uncertain == 1
    assert journal.state("t1") == "launched"
    with sqlite3.connect(journal_path) as db:
        db.execute("UPDATE launches SET replay_fingerprint=?, launched_at=? WHERE task_id='t1'",
                   (run.replay_fingerprint(control.tasks["t1"]), 1.0))
    assert poll_once(**common).uncertain == 1
    assert journal.state("t1") == "launched"
