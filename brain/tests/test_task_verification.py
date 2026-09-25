"""Trusted local artifact checks, independent of CLI self-reports."""

import hashlib
import json
from pathlib import Path

import pytest

from app.pc_connector import make_terminal_manager
from app.task_verification import make_task_verifier
from app.terminal_worker import TerminalEvidence, TerminalTaskResult


def _policy(root, task_id, checks):
    return {"verification": {"root": str(root),
                              "by_task_id": {task_id: checks}}}


def test_exact_task_id_and_all_file_checks_must_match(tmp_path):
    (tmp_path / "report.txt").write_text("done\n", encoding="utf-8")
    digest = hashlib.sha256(b"done\n").hexdigest()
    policy = _policy(tmp_path, "trusted-task", [
        {"kind": "file_exists", "path": "report.txt"},
        {"kind": "file_equals", "path": "report.txt", "text": "done\n"},
        {"kind": "file_sha256", "path": "report.txt", "sha256": digest},
    ])
    verify = make_task_verifier(policy)
    assert verify({"task_id": "trusted-task"}) is True
    assert verify({"task_id": "other-task"}) is False
    (tmp_path / "report.txt").write_text("changed\n", encoding="utf-8")
    assert verify({"task_id": "trusted-task"}) is False


def test_symlinks_cannot_escape_verification_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("done", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    (root / "subdir").symlink_to(tmp_path, target_is_directory=True)
    verify = make_task_verifier(_policy(root, "t1", [
        {"kind": "file_equals", "path": "link.txt", "text": "done"}]))
    assert verify({"task_id": "t1"}) is False
    verify_directory = make_task_verifier(_policy(root, "t1", [
        {"kind": "file_equals", "path": "subdir/outside.txt", "text": "done"}]))
    assert verify_directory({"task_id": "t1"}) is False


@pytest.mark.parametrize("check", [
    {"kind": "file_exists", "path": "../outside"},
    {"kind": "file_exists", "path": "/absolute"},
    {"kind": "file_exists", "path": "a//b"},
    {"kind": "file_exists", "path": "a\\b"},
    {"kind": "file_sha256", "path": "report", "sha256": "bad"},
    {"kind": "file_equals", "path": "report", "text": "x", "command": "true"},
    {"kind": "shell", "command": "true"},
])
def test_unsafe_or_unknown_checks_are_rejected_at_configuration_time(tmp_path, check):
    with pytest.raises(ValueError):
        make_task_verifier(_policy(tmp_path, "t1", [check]))


def test_terminal_manager_uses_local_policy_and_never_cli_report_as_verifier(
        tmp_path, monkeypatch):
    report = {"status": "completed", "summary": "claimed success",
              "tools_used": ["terminal"],
              "evidence": [{"what": "marker", "check": "read", "outcome": "passed"}],
              "next_action": ""}
    marker = tmp_path / "marker.txt"

    def fake_run(*args, **kwargs):
        evidence = TerminalEvidence(0, "", "", json.dumps(report))
        return TerminalTaskResult(evidence, kwargs["verify"](evidence))

    monkeypatch.setattr("app.pc_connector.run_terminal_task", fake_run)
    config = {"argv": ["synthetic", "{prompt_file}"], "workdir": str(tmp_path),
              "verification": {"by_task_id": {"trusted-task": [
                  {"kind": "file_equals", "path": "marker.txt", "text": "ready"}
              ]}}}
    manager = make_terminal_manager(config)
    task = {"task_id": "trusted-task", "envelope": {
        "goal": "write marker", "authority": "test", "success_criteria": "ready"}}
    assert manager(task)["result"]["reason"] == "verification_failed"
    marker.write_text("ready", encoding="utf-8")
    assert manager(task)["status"] == "completed"
    other = {**task, "task_id": "unconfigured-task"}
    assert manager(other)["result"]["reason"] == "verification_failed"


def test_hermes_main_receives_same_local_policy(tmp_path, monkeypatch):
    from app import hermes_runs_manager, pc_connector

    marker = tmp_path / "marker.txt"
    marker.write_text("ready", encoding="utf-8")
    config = {"manager_kind": "hermes", "driver": "hermes_runs",
              "verification": {"root": str(tmp_path), "by_task_id": {"t1": [
                  {"kind": "file_equals", "path": "marker.txt", "text": "ready"}
              ]}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    captured = {}

    def fake_factory(**kwargs):
        captured["verify"] = kwargs["verify"]
        return lambda task: {}

    monkeypatch.setattr(hermes_runs_manager, "make_hermes_runs_manager", fake_factory)
    monkeypatch.setattr(pc_connector, "poll_once", lambda **kwargs: pc_connector.PollReport())
    assert pc_connector.main(["--config", str(config_path),
                              "--base-url", "https://control.example"]) == 0
    assert captured["verify"]({"task_id": "t1"}, {}, {}) is True
    assert captured["verify"]({"task_id": "t2"}, {}, {}) is False
