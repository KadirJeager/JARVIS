"""Bounded Hermes Runs manager for the PC connector's ``run_manager`` callback.

This uses Hermes's own agent/tool loop. The task supplies only a goal envelope;
the endpoint, credential and verifier are trusted local configuration. Hermes
run completion and the manager's report do not independently prove the task.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable, Mapping
from urllib import parse

from .harness_prompt import render_manager_prompt
from .harness_report import ManagerResultError, manager_report
from .pc_connector import ConnectorError, UrllibJSONClient
from .terminal_worker import TerminalEvidence, TerminalTaskResult


_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_ACTIVE = frozenset({"started", "queued", "running", "stopping"})
_MAX_OUTPUT_BYTES = 64 * 1024
_REPORT_INSTRUCTION = ("\n\nReturn only one JSON object with keys: status "
    "(completed, waiting_user, or failed), summary (string), tools_used "
    "(array of strings), evidence (array of objects with what, check, and "
    "outcome: passed, failed, or unknown), next_action (string).")


def _local_url(value: str) -> str:
    parsed = parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in
            {"localhost", "127.0.0.1", "::1"} or not parsed.netloc or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Hermes Runs URL must be local HTTP")
    return value.rstrip("/")


def _idempotency_key(task_id: str) -> str:
    # Fixed printable ASCII, namespaced separately from any other Hermes client.
    return "jarvis-task-" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()


def make_hermes_runs_manager(*, base_url: str = "http://127.0.0.1:8642",
                             api_key: str | None = None,
                             verify: Callable[[dict, dict, dict], bool] | None = None,
                             allow_agent_report: bool = False,
                             client: Any = None, poll_interval: float = 2.0,
                             max_wait_seconds: float = 600.0,
                             request_timeout: float = 15.0,
                             sleep: Callable[[float], None] = time.sleep,
                             monotonic: Callable[[], float] = time.monotonic):
    """Return a ``poll_once(run_manager=...)`` callback.

    ``verify(task, hermes_status, report)`` checks an external artifact or
    state. Trusted local configuration can instead permit an agent-reported
    completion; the result records that lower assurance explicitly.
    A timeout raises so the PC journal remains uncertain rather than relaunching
    work. The caller must reconcile such tasks before any manual retry.
    """
    root = _local_url(base_url)
    if poll_interval <= 0 or max_wait_seconds <= 0 or request_timeout <= 0:
        raise ValueError("Hermes Runs timeouts must be positive")
    token = api_key if api_key is not None else os.getenv("HERMES_API_SERVER_KEY")
    if not token:
        raise ConnectorError("Hermes API key is not configured")
    http = client if client is not None else UrllibJSONClient()

    def call(method: str, path: str, *, body: Mapping[str, Any] | None = None,
             idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": "Bearer " + token,
                   "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = http.request(method, root + path, headers=headers,
                                    json_body=body, timeout=request_timeout)
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("Hermes Runs API request failed") from None
        if not isinstance(response, dict):
            raise ConnectorError("Hermes Runs API returned invalid JSON")
        return response

    def _body(task: dict) -> tuple[dict, str]:
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task requires task_id")
        prompt = render_manager_prompt(task) + _REPORT_INSTRUCTION
        body = {"input": prompt, "session_id": "jarvis-" +
                hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:32]}
        return body, task_id

    def replay_fingerprint(task: dict) -> str:
        body, _ = _body(task)
        # The Hermes idempotency namespace includes API profile + credential.
        # Bind local recovery to the same endpoint, credential and exact body.
        wire = json.dumps({"url": root, "api_key": token, "body": body},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

    def run(task: dict) -> dict:
        body, task_id = _body(task)
        capabilities = call("GET", "/v1/capabilities")
        features = capabilities.get("features")
        idem = features.get("runs_idempotency") if isinstance(features, dict) else None
        if (not isinstance(idem, dict) or idem.get("supported") is not True or
                idem.get("durable") is not True or
                isinstance(idem.get("retention_seconds"), bool) or
                not isinstance(idem.get("retention_seconds"), (int, float)) or
                idem["retention_seconds"] < 24 * 3600):
            raise ConnectorError("Hermes durable run idempotency is unavailable")
        key = _idempotency_key(task_id)
        # A lost HTTP response can be retried with this exact body/key. Hermes
        # reserves the key before work starts and rejects payload conflicts.
        acceptance = call("POST", "/v1/runs", body=body, idempotency_key=key)
        run_id = acceptance.get("run_id")
        if not isinstance(run_id, str) or not run_id.startswith("run_"):
            raise ConnectorError("Hermes Runs API returned invalid run ID")
        deadline = monotonic() + max_wait_seconds
        while True:
            status = call("GET", "/v1/runs/" + parse.quote(run_id, safe=""))
            if status.get("run_id") != run_id:
                raise ConnectorError("Hermes Runs API returned mismatched run ID")
            state = status.get("status")
            session_id = status.get("session_id")
            if not isinstance(session_id, str) or not session_id.strip():
                raise ConnectorError("Hermes Runs API omitted session ID")
            if state == "waiting_for_approval":
                return {"status": "waiting_user", "session_id": session_id,
                        "waiting_for": "Hermes bu görev için araç kullanım onayı bekliyor; "
                        "run_id=" + run_id}
            if state in _TERMINAL:
                break
            if state not in _ACTIVE:
                raise ConnectorError("Hermes Runs API returned unknown status")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ConnectorError("Hermes run did not finish in time")
            sleep(min(poll_interval, remaining))

        if state != "completed":
            return {"status": "failed", "session_id": session_id,
                    "result": {"reason": "hermes_run_" + state,
                               "hermes_run_id": run_id}}
        output = status.get("output")
        if (not isinstance(output, str) or
                len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES):
            return {"status": "failed", "session_id": session_id,
                    "result": {"reason": "invalid_manager_report",
                               "hermes_run_id": run_id}}
        try:
            report = manager_report(TerminalTaskResult(
                TerminalEvidence(0, "", "", None, output), False))
        except ManagerResultError:
            return {"status": "failed", "session_id": session_id,
                    "result": {"reason": "invalid_manager_report",
                               "hermes_run_id": run_id}}
        if report["status"] == "waiting_user":
            if not report["next_action"].strip():
                return {"status": "failed", "session_id": session_id,
                        "result": {"reason": "invalid_manager_report",
                                   "hermes_run_id": run_id}}
            return {"status": "waiting_user", "session_id": session_id,
                    "waiting_for": report["next_action"]}
        if report["status"] != "completed":
            return {"status": "failed", "session_id": session_id,
                    "result": {"reason": "manager_failed", "summary": report["summary"],
                               "hermes_run_id": run_id}}
        reported_pass = bool(report["evidence"]) and all(
            item["outcome"] == "passed" for item in report["evidence"])
        if not reported_pass:
            return {"status": "failed", "session_id": session_id,
                    "result": {"reason": "verification_failed", "summary": report["summary"],
                               "hermes_run_id": run_id}}
        try:
            independently_verified = verify(task, status, report) is True if verify else False
        except Exception:
            independently_verified = False
        if not independently_verified and (verify is not None or not allow_agent_report):
            return {"status": "failed", "session_id": session_id,
                    "result": {"reason": "verification_failed", "summary": report["summary"],
                               "hermes_run_id": run_id}}
        return {"status": "completed", "session_id": session_id,
                "result": {"summary": report["summary"],
                           "hermes_run_id": run_id,
                           "tools_used": report["tools_used"],
                           "evidence": report["evidence"],
                           "verification": ("independent" if independently_verified
                                            else "agent_reported")}}

    run.replay_fingerprint = replay_fingerprint
    return run
