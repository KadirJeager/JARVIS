"""Validate the CLI-neutral manager's structured result without cloud imports."""

import json

from .terminal_worker import TerminalTaskResult


class ManagerResultError(ValueError):
    """The manager result did not meet the task-report contract."""


def manager_report(result: TerminalTaskResult) -> dict:
    raw = result.evidence.result_payload or result.evidence.result_file
    if not raw:
        raise ManagerResultError("manager did not write a result")
    try:
        report = json.loads(raw)
    except ValueError:
        raise ManagerResultError("manager result is not JSON") from None
    if not isinstance(report, dict):
        raise ManagerResultError("manager result is not an object")
    if report.get("status") not in {"completed", "waiting_user", "failed"}:
        raise ManagerResultError("manager status is invalid")
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        raise ManagerResultError("manager summary is missing")
    if not isinstance(report.get("tools_used"), list) or not all(
        isinstance(tool, str) for tool in report["tools_used"]
    ):
        raise ManagerResultError("manager tools_used is invalid")
    if not isinstance(report.get("evidence"), list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("what"), str)
        and isinstance(item.get("check"), str)
        and item.get("outcome") in {"passed", "failed", "unknown"}
        for item in report["evidence"]
    ):
        raise ManagerResultError("manager evidence is invalid")
    if not isinstance(report.get("next_action"), str):
        raise ManagerResultError("manager next_action is invalid")
    return report
