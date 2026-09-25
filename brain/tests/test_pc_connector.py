"""Synthetic one-shot PC delivery; no network, model, or real CLI calls."""

from copy import deepcopy
from urllib.parse import urlsplit

import pytest

from app.pc_connector import (ConnectorError, LaunchJournal, UrllibJSONClient,
                              make_terminal_manager, poll_once)
from app.terminal_worker import TerminalEvidence, TerminalTaskResult


class FakeControl:
    def __init__(self, tasks):
        self.tasks = {task["task_id"]: deepcopy(task) for task in tasks}
        self.posts = []
        self.fail_terminal_once = False

    def request(self, method, url, *, headers, json_body=None, timeout=15):
        assert headers["Authorization"] == "Bearer synthetic-token"
        path = urlsplit(url).path
        if method == "GET" and path == "/v1/tasks/queued":
            return {"tasks": [deepcopy(task) for task in self.tasks.values()
                              if task["status"] == "queued"]}
        key = path.split("/")[3]
        task = self.tasks[key]
        if method == "GET" and path == f"/v1/tasks/{key}":
            return {"task": deepcopy(task)}
        if method == "POST" and path.endswith("/claim"):
            claimed = task["status"] == "queued"
            if claimed:
                task["status"] = "claimed"
            return {"task": deepcopy(task), "claimed": claimed}
        if method == "POST" and path.endswith("/outcome"):
            body = deepcopy(json_body)
            self.posts.append((key, body))
            if body["status"] == "running":
                if task["status"] not in ("claimed", "running"):
                    raise ConnectorError("invalid running state")
                task["status"] = "running"
                task["harness_run_id"] = body["run_id"]
            else:
                if self.fail_terminal_once:
                    self.fail_terminal_once = False
                    raise ConnectorError("simulated lost response")
                task["status"] = body["status"]
                task["result"] = body.get("result")
            return {"task": deepcopy(task)}
        raise AssertionError((method, path))


def task(key, *, kind="codex"):
    return {"task_id": key, "status": "queued",
            "delivery_target": {"kind": kind, "device": "pc"},
            "envelope": {"goal": "synthetic"}}


def poll(tmp_path, client, runner, **kwargs):
    return poll_once(
        base_url="https://control.example", manager_kind="codex",
        journal_path=tmp_path / "journal.sqlite3", run_manager=runner,
        worker_token="synthetic-token", client=client, **kwargs,
    )


def test_claim_launch_and_verified_outcome_once(tmp_path):
    client = FakeControl([task("t1")])
    seen = []
    report = poll(tmp_path, client, lambda item: seen.append(item["task_id"]) or {
        "status": "completed", "result": {"summary": "checked"},
    })
    assert report.launched == 1
    assert seen == ["t1"]
    assert [body["status"] for _, body in client.posts] == ["running", "completed"]
    assert client.tasks["t1"]["result"] == {"summary": "checked"}
    assert poll(tmp_path, client, lambda _: pytest.fail("ran twice")).launched == 0


def test_outcome_replayed_after_lost_response_without_second_launch(tmp_path):
    client = FakeControl([task("t1")])
    client.fail_terminal_once = True
    seen = []
    with pytest.raises(ConnectorError, match="lost response"):
        poll(tmp_path, client, lambda item: seen.append(item["task_id"]) or {
            "status": "completed", "result": {"summary": "done"},
        })
    assert LaunchJournal(tmp_path / "journal.sqlite3").state("t1") == "outcome"
    report = poll(tmp_path, client, lambda _: pytest.fail("ran twice"))
    assert report.replayed == 1
    assert report.launched == 0
    assert seen == ["t1"]
    assert [body["status"] for _, body in client.posts] == [
        "running", "completed", "completed"]


def test_uncertain_runner_is_never_reexecuted(tmp_path):
    client = FakeControl([task("t1")])
    seen = []

    def crash(item):
        seen.append(item["task_id"])
        raise RuntimeError("possibly made side effects")

    assert poll(tmp_path, client, crash).uncertain == 1
    assert LaunchJournal(tmp_path / "journal.sqlite3").state("t1") == "launched"
    assert poll(tmp_path, client, lambda _: pytest.fail("ran twice")).launched == 0
    assert seen == ["t1"]
    assert client.tasks["t1"]["status"] == "running"


def test_ready_claim_recovers_without_reclaim(tmp_path):
    client = FakeControl([task("t1")])
    client.tasks["t1"]["status"] = "claimed"
    journal = LaunchJournal(tmp_path / "journal.sqlite3")
    journal.prepare("t1")
    journal.ready("t1")
    seen = []
    report = poll(tmp_path, client, lambda item: seen.append(item["task_id"]) or {
        "status": "failed", "result": {"reason": "synthetic"},
    })
    assert report.launched == 1
    assert seen == ["t1"]
    assert client.tasks["t1"]["status"] == "failed"


def test_lost_claim_response_is_visible_but_never_launched(tmp_path):
    client = FakeControl([task("t1")])
    client.tasks["t1"]["status"] = "claimed"
    LaunchJournal(tmp_path / "journal.sqlite3").prepare("t1")
    report = poll(tmp_path, client, lambda _: pytest.fail("ambiguous claim launched"))
    assert report.uncertain == 1
    assert report.launched == 0
    assert client.posts == []


def test_filters_unselected_manager_before_claim(tmp_path):
    client = FakeControl([task("other", kind="agy"), task("chosen")])
    report = poll(tmp_path, client, lambda _: {
        "status": "completed", "result": {},
    })
    assert report.launched == 1
    assert report.skipped == 1
    assert client.tasks["other"]["status"] == "queued"
    assert client.tasks["chosen"]["status"] == "completed"


def test_invalid_manager_outcome_stays_uncertain(tmp_path):
    client = FakeControl([task("t1")])
    report = poll(tmp_path, client, lambda _: {
        "status": "completed", "result": {}, "run_id": "wrong",
    })
    assert report.uncertain == 1
    assert LaunchJournal(tmp_path / "journal.sqlite3").state("t1") == "launched"
    assert client.tasks["t1"]["status"] == "running"


def test_http_is_only_allowed_for_exact_local_host(tmp_path):
    client = FakeControl([])
    with pytest.raises(ValueError, match="HTTPS or local HTTP"):
        poll_once(base_url="http://localhost.evil.example", manager_kind="codex",
                  journal_path=tmp_path / "journal.sqlite3", run_manager=lambda _: {},
                  worker_token="synthetic-token", client=client)


def test_production_terminal_wrapper_needs_independent_verification(tmp_path, monkeypatch):
    report = {"status": "completed", "summary": "claimed success",
              "tools_used": ["terminal"],
              "evidence": [{"what": "marker", "check": "read", "outcome": "passed"}],
              "next_action": ""}

    def fake_run(*args, **kwargs):
        verifier = kwargs["verify"]
        return TerminalTaskResult(
            TerminalEvidence(0, "", "", __import__("json").dumps(report)),
            verifier is not None and verifier(None),
        )

    monkeypatch.setattr("app.pc_connector.run_terminal_task", fake_run)
    config = {"argv": ["synthetic", "{prompt_file}"], "workdir": str(tmp_path)}
    record = {"task_id": "t1", "envelope": {
        "goal": "read marker", "authority": "read only",
        "success_criteria": ["marker read"],
    }}
    unverified = make_terminal_manager(config)(record)
    assert unverified["status"] == "failed"
    assert unverified["result"]["reason"] == "verification_failed"
    verified = make_terminal_manager(config, verify=lambda _: True)(record)
    assert verified["status"] == "completed"
    assert verified["result"]["verification"] == "independent"
    reported = make_terminal_manager({**config, "completion_policy": "agent_reported"})(record)
    assert reported["status"] == "completed"
    assert reported["result"]["verification"] == "agent_reported"
    rejected = make_terminal_manager({**config, "completion_policy": "agent_reported"},
                                     verify=lambda _: False)(record)
    assert rejected["status"] == "failed"


def test_terminal_manager_passes_generic_stream_configuration(tmp_path, monkeypatch):
    report = {"status": "completed", "summary": "marker matched",
              "tools_used": ["read_file"],
              "evidence": [{"what": "marker", "check": "exact", "outcome": "passed"}],
              "next_action": ""}
    selected = {"event_field": "event", "event_value": "result",
                "payload_path": ["result", "structured_output"],
                "status_path": ["result", "status"], "success_value": "SUCCESS"}
    template = {"event": "user", "message": {"content": "{prompt}"}}

    def fake_run(*args, **kwargs):
        assert kwargs["stdin_json_template"] == template
        assert kwargs["stdout_result"] == selected
        assert kwargs["verify"] is not None
        return TerminalTaskResult(
            TerminalEvidence(0, "", "", None, __import__("json").dumps(report)), True)

    monkeypatch.setattr("app.pc_connector.run_terminal_task", fake_run)
    config = {"argv": ["synthetic", "--input-format", "stream-json"],
              "workdir": str(tmp_path), "stdin_json_template": template,
              "stdout_result": selected}
    outcome = make_terminal_manager(config, verify=lambda _: True)(
        {"task_id": "t1", "envelope": {"goal": "read marker",
          "authority": "read only", "success_criteria": ["marker read"]}})
    assert outcome["status"] == "completed"
    assert outcome["result"]["summary"] == "marker matched"


def test_http_client_rejects_redirect_without_following(monkeypatch):
    from urllib import error, request

    called = []

    class FakeOpener:
        def open(self, req, timeout):
            called.append(req.full_url)
            raise error.HTTPError(req.full_url, 302, "redirect", {
                "Location": "https://other.example/collect"
            }, None)

    def build_opener(handler):
        assert handler.redirect_request(None, None, 302, "redirect", {},
                                        "https://other.example/collect") is None
        return FakeOpener()

    monkeypatch.setattr(request, "build_opener", build_opener)
    with pytest.raises(ConnectorError, match="HTTP 302"):
        UrllibJSONClient().request(
            "GET", "https://control.example/v1/tasks/queued",
            headers={"Authorization": "Bearer synthetic-token"},
        )
    assert called == ["https://control.example/v1/tasks/queued"]
