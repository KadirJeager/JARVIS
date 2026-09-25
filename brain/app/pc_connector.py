"""One-shot PC handoff for a trusted, terminal-driven manager harness.

The task chooses *what* to do. The caller's ``run_manager`` callback chooses
the installed CLI and its trusted argv; no command is accepted from the task.
The local journal records launch intent before the callback, so a process crash
can leave a task uncertain but cannot silently execute it twice.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping
from urllib import error, parse, request

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from . import harness_prompt
from .harness_report import manager_report
from .task_verification import make_task_verifier
from .terminal_worker import TerminalEvidence, run_terminal_task


class ConnectorError(RuntimeError):
    """A failure reason that excludes bearer tokens and task content."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the control-plane bearer token to a redirected host.
        return None


class UrllibJSONClient:
    """Small default HTTP client; tests can inject an equivalent client."""

    def request(self, method: str, url: str, *, headers: Mapping[str, str],
                json_body: Mapping[str, Any] | None = None,
                timeout: float = 15) -> dict:
        payload = (json.dumps(json_body, ensure_ascii=False).encode("utf-8")
                   if json_body is not None else None)
        req = request.Request(url, data=payload, headers=dict(headers), method=method)
        try:
            with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise ConnectorError("control API rejected the request")
                body = response.read(4 * 1024 * 1024 + 1)
        except error.HTTPError as exc:
            raise ConnectorError(f"control API returned HTTP {exc.code}") from None
        except (error.URLError, TimeoutError, OSError):
            raise ConnectorError("control API is unavailable") from None
        if len(body) > 4 * 1024 * 1024:
            raise ConnectorError("control API response exceeded size limit")
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ConnectorError("control API returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ConnectorError("control API returned invalid JSON")
        return decoded


class LaunchJournal:
    """SQLite write-ahead record for one PC worker installation."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS launches (
                task_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN
                    ('prepared', 'ready', 'launched', 'outcome')),
                outcome_json TEXT,
                launched_at REAL,
                replay_fingerprint TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(launches)")}
            if "launched_at" not in columns:
                db.execute("ALTER TABLE launches ADD COLUMN launched_at REAL")
            if "replay_fingerprint" not in columns:
                db.execute("ALTER TABLE launches ADD COLUMN replay_fingerprint TEXT")

    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def state(self, task_id: str) -> str | None:
        with self._connection() as db:
            row = db.execute("SELECT state FROM launches WHERE task_id=?", (task_id,)).fetchone()
            return row[0] if row else None

    def prepare(self, task_id: str) -> str:
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO launches(task_id,state) VALUES(?,'prepared')",
                       (task_id,))
            row = db.execute("SELECT state FROM launches WHERE task_id=?", (task_id,)).fetchone()
            return row[0]

    def ready(self, task_id: str) -> None:
        self._advance(task_id, "prepared", "ready")

    def launched(self, task_id: str, *, replay_fingerprint: str | None = None) -> bool:
        """Return true only for the process that atomically wrote launch intent."""
        with self._connection() as db:
            changed = db.execute(
                "UPDATE launches SET state='launched', launched_at=?, replay_fingerprint=? "
                "WHERE task_id=? AND state='ready'",
                (time.time(), replay_fingerprint, task_id),
            ).rowcount
            return changed == 1

    @contextmanager
    def execution_lock(self):
        """Serialize manager calls across connector processes; crash releases lock."""
        lock_path = self.path.with_name(self.path.name + ".execution.lock")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        locked = False
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            if os.name == "nt":
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _advance(self, task_id: str, old: str, new: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE launches SET state=? WHERE task_id=? AND state=?",
                       (new, task_id, old))

    def capture(self, task_id: str, outcome: Mapping[str, Any]) -> None:
        encoded = json.dumps(dict(outcome), ensure_ascii=False, separators=(",", ":"))
        with self._connection() as db:
            changed = db.execute(
                "UPDATE launches SET state='outcome', outcome_json=? "
                "WHERE task_id=? AND state='launched'", (encoded, task_id),
            ).rowcount
            if changed != 1:
                raise ConnectorError("task launch journal changed unexpectedly")

    def pending(self) -> list[tuple[str, dict]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT task_id,outcome_json FROM launches WHERE state='outcome' ORDER BY task_id"
            ).fetchall()
        return [(key, json.loads(value)) for key, value in rows]

    def ready_tasks(self) -> list[str]:
        """Claims known to have succeeded, with no local launch intent yet."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT task_id FROM launches WHERE state='ready' ORDER BY task_id"
            ).fetchall()
        return [row[0] for row in rows]

    def prepared_tasks(self) -> list[str]:
        """Claims for which the response may have been lost."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT task_id FROM launches WHERE state='prepared' ORDER BY task_id"
            ).fetchall()
        return [row[0] for row in rows]

    def launched_tasks(self) -> list[tuple[str, float | None, str | None]]:
        """Unsettled launch intents; legacy rows lack safe replay age evidence."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT task_id,launched_at,replay_fingerprint FROM launches "
                "WHERE state='launched' "
                "ORDER BY task_id"
            ).fetchall()
        return [(key, timestamp, fingerprint) for key, timestamp, fingerprint in rows]

    def delivered(self, task_id: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM launches WHERE task_id=? AND state='outcome'", (task_id,))


@dataclass(frozen=True)
class PollReport:
    launched: int = 0
    replayed: int = 0
    uncertain: int = 0
    skipped: int = 0


def _outcome(value: Mapping[str, Any]) -> dict:
    if not isinstance(value, Mapping):
        raise ConnectorError("manager returned an invalid outcome")
    body = dict(value)
    if set(body) - {"status", "run_id", "session_id", "waiting_for", "result"}:
        raise ConnectorError("manager returned an invalid outcome")
    if body.get("status") not in {"waiting_user", "completed", "failed", "cancelled"}:
        raise ConnectorError("manager returned an invalid outcome")
    if body["status"] == "waiting_user":
        if not isinstance(body.get("waiting_for"), str) or not body["waiting_for"].strip():
            raise ConnectorError("manager returned an invalid outcome")
        if body.get("result") is not None:
            raise ConnectorError("manager returned an invalid outcome")
    elif not isinstance(body.get("result"), dict) or body.get("waiting_for") is not None:
        raise ConnectorError("manager returned an invalid outcome")
    if body.get("run_id") is not None and not isinstance(body["run_id"], str):
        raise ConnectorError("manager returned an invalid outcome")
    if body.get("session_id") is not None and (
        not isinstance(body["session_id"], str) or not body["session_id"].strip()
    ):
        raise ConnectorError("manager returned an invalid outcome")
    return body


def poll_once(*, base_url: str, manager_kind: str, journal_path: Path,
              run_manager: Callable[[dict], Mapping[str, Any]],
              reconcile_manager: Callable[[dict], Mapping[str, Any]] | None = None,
              worker_token: str | None = None, client=None,
              limit: int = 20, timeout: float = 15) -> PollReport:
    """Poll and run each eligible task at most once on this PC.

    ``run_manager`` receives the claimed task and returns a control API outcome
    body. It must use trusted local CLI configuration and independently verify
    the work before reporting completion. No model credential is used here.
    """
    parsed_url = parse.urlsplit(base_url)
    if (parsed_url.scheme not in ("https", "http") or not parsed_url.netloc
            or parsed_url.username is not None or parsed_url.password is not None
            or parsed_url.query or parsed_url.fragment or parsed_url.path not in ("", "/")
            or (parsed_url.scheme == "http" and parsed_url.hostname not in
                ("localhost", "127.0.0.1", "::1"))):
        raise ValueError("control API URL must be HTTPS or local HTTP")
    if not manager_kind or not manager_kind.strip():
        raise ValueError("manager_kind must be nonempty")
    if not 1 <= limit <= 100 or timeout <= 0:
        raise ValueError("invalid poll limit or timeout")
    token = worker_token if worker_token is not None else os.getenv("JARVIS_WORKER_TOKEN")
    if not token:
        raise ConnectorError("worker token is not configured")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    http = client if client is not None else UrllibJSONClient()
    journal = LaunchJournal(journal_path)
    root = base_url.rstrip("/")

    def call(method: str, path: str, body: Mapping[str, Any] | None = None) -> dict:
        try:
            response = http.request(method, root + path, headers=headers,
                                    json_body=body, timeout=timeout)
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("control API request failed") from None
        if not isinstance(response, dict):
            raise ConnectorError("control API returned an invalid response")
        return response

    replayed = 0
    for key, body in journal.pending():
        call("POST", "/v1/tasks/" + parse.quote(key, safe="") + "/outcome", body)
        journal.delivered(key)
        replayed += 1

    launched = uncertain = skipped = 0

    # A prepared record means the claim call may have reached the server, but
    # this process did not observe success. Such a task is never launched.
    for key in journal.prepared_tasks():
        response = call("GET", "/v1/tasks/" + parse.quote(key, safe=""))
        task = response.get("task")
        if not isinstance(task, dict) or task.get("task_id") != key:
            raise ConnectorError("control API returned an invalid task")
        if task.get("status") in ("claimed", "running", "waiting_user"):
            uncertain += 1

    def run_claimed(key: str, task: dict) -> None:
        nonlocal launched, uncertain, skipped
        with journal.execution_lock():
            path = "/v1/tasks/" + parse.quote(key, safe="") + "/outcome"
            call("POST", path, {"status": "running", "run_id": key})
            fingerprinter = getattr(run_manager, "replay_fingerprint", None)
            fingerprint = fingerprinter(task) if callable(fingerprinter) else None
            if not journal.launched(key, replay_fingerprint=fingerprint):
                skipped += 1
                return
            launched += 1
            try:
                result = _outcome(run_manager(task))
                if result.get("run_id") not in (None, key):
                    raise ConnectorError("manager returned a mismatched run ID")
                result["run_id"] = key
                journal.capture(key, result)
            except Exception:
                # Only an explicitly replay-safe manager may reconcile this.
                uncertain += 1
                return
            call("POST", path, result)
            journal.delivered(key)

    if reconcile_manager is not None:
        # Hermes retains keyed runs for 24h after their last status update.
        # Retry only inside 23h from launch, leaving a safety margin. A row
        # created by the old journal schema has no timestamp and stays uncertain.
        for key, launched_at, fingerprint in journal.launched_tasks():
            if (launched_at is None or fingerprint is None or
                    not 0 <= time.time() - launched_at < 23 * 3600):
                uncertain += 1
                continue
            with journal.execution_lock():
                if journal.state(key) != "launched":
                    continue
                if not 0 <= time.time() - launched_at < 23 * 3600:
                    uncertain += 1
                    continue
                response = call("GET", "/v1/tasks/" + parse.quote(key, safe=""))
                task = response.get("task")
                if not isinstance(task, dict) or task.get("task_id") != key:
                    raise ConnectorError("control API returned an invalid task")
                target = task.get("delivery_target")
                if (task.get("status") not in ("claimed", "running") or
                        not isinstance(target, dict) or target.get("device") != "pc" or
                        target.get("kind") != manager_kind):
                    uncertain += 1
                    continue
                fingerprinter = getattr(reconcile_manager, "replay_fingerprint", None)
                if not callable(fingerprinter) or fingerprinter(task) != fingerprint:
                    uncertain += 1
                    continue
                try:
                    result = _outcome(reconcile_manager(task))
                    if result.get("run_id") not in (None, key):
                        raise ConnectorError("manager returned a mismatched run ID")
                    result["run_id"] = key
                    journal.capture(key, result)
                except Exception:
                    uncertain += 1
                    continue
                call("POST", "/v1/tasks/" + parse.quote(key, safe="") + "/outcome", result)
                journal.delivered(key)
                replayed += 1

    for key in journal.ready_tasks():
        response = call("GET", "/v1/tasks/" + parse.quote(key, safe=""))
        task = response.get("task")
        if not isinstance(task, dict) or task.get("task_id") != key:
            raise ConnectorError("control API returned an invalid task")
        target = task.get("delivery_target")
        if (not isinstance(target, dict) or target.get("device") != "pc"
                or target.get("kind") != manager_kind):
            skipped += 1
            continue
        if task.get("status") in ("claimed", "running"):
            run_claimed(key, task)
        else:
            skipped += 1

    tasks = call("GET", f"/v1/tasks/queued?limit={limit}").get("tasks")
    if not isinstance(tasks, list):
        raise ConnectorError("control API returned an invalid task list")
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            raise ConnectorError("control API returned an invalid task")
        target = task.get("delivery_target")
        if (not isinstance(target, dict) or target.get("device") != "pc"
                or target.get("kind") != manager_kind):
            skipped += 1
            continue
        key = task["task_id"]
        state = journal.prepare(key)
        if state != "prepared":
            skipped += 1
            continue
        claimed_response = call("POST", "/v1/tasks/" + parse.quote(key, safe="") + "/claim")
        if claimed_response.get("claimed") is not True:
            skipped += 1
            continue
        claimed_task = claimed_response.get("task")
        if not isinstance(claimed_task, dict) or claimed_task.get("task_id") != key:
            raise ConnectorError("control API returned an invalid claim")
        journal.ready(key)
        run_claimed(key, claimed_task)
    return PollReport(launched, replayed, uncertain, skipped)


def make_terminal_manager(config: Mapping[str, Any], *,
                          verify: Callable[[TerminalEvidence], bool] | None = None):
    """Bind trusted local argv to the shared CLI-neutral runner.

    The default verifier is absent: a manager's self-reported success cannot
    independently prove task completion. A caller can inject a verifier for a
    concrete task type; this module never takes executable argv from a task.
    """
    argv = config.get("argv")
    workdir = config.get("workdir")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("config.argv must be a nonempty string list")
    if not isinstance(workdir, str) or not workdir.strip():
        raise ValueError("config.workdir must be a directory")
    workdir_path = Path(workdir).expanduser().resolve(strict=True)
    if not workdir_path.is_dir():
        raise ValueError("config.workdir must be a directory")
    preflight_argv = config.get("preflight_argv")
    if preflight_argv is not None and (
        not isinstance(preflight_argv, list) or
        not all(isinstance(x, str) for x in preflight_argv)
    ):
        raise ValueError("config.preflight_argv must be a string list")
    preflight_expected = config.get("preflight_expected")
    if preflight_expected is not None and not isinstance(preflight_expected, str):
        raise ValueError("config.preflight_expected must be a string")
    stdin_prompt = config.get("stdin_prompt", False)
    if not isinstance(stdin_prompt, bool):
        raise ValueError("config.stdin_prompt must be bool")
    stdin_json_template = config.get("stdin_json_template")
    if stdin_json_template is not None and not isinstance(stdin_json_template, dict):
        raise ValueError("config.stdin_json_template must be an object")
    stdout_result = config.get("stdout_result")
    if stdout_result is not None and not isinstance(stdout_result, dict):
        raise ValueError("config.stdout_result must be an object")
    timeout_seconds = config.get("timeout_seconds", 120)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("config.timeout_seconds must be positive")
    completion_policy = config.get("completion_policy", "verified")
    if completion_policy not in ("verified", "agent_reported"):
        raise ValueError("config.completion_policy is invalid")
    task_verifier = make_task_verifier(config, default_root=workdir)

    def run(task: dict) -> dict:
        prompt = harness_prompt.render_manager_prompt(task)
        independent_check = (verify if verify is not None else
                             (lambda _evidence: task_verifier(task))
                             if task_verifier is not None else None)
        result = run_terminal_task(
            prompt, workdir_path, argv=argv, verify=independent_check,
            preflight_argv=preflight_argv, preflight_expected=preflight_expected,
            stdin_prompt=stdin_prompt, stdin_json_template=stdin_json_template,
            stdout_result=stdout_result, timeout_seconds=timeout_seconds,
        )
        report = manager_report(result)
        status = report["status"]
        if status == "waiting_user":
            question = report["next_action"].strip()
            if not question:
                raise ConnectorError("manager omitted the needed user action")
            return {"status": "waiting_user", "waiting_for": question}
        if status == "completed":
            passed = bool(report["evidence"]) and all(
                item["outcome"] == "passed" for item in report["evidence"]
            )
            if passed and (result.task_verified or
                           (completion_policy == "agent_reported" and independent_check is None)):
                return {"status": "completed", "result": {
                    "summary": report["summary"], "tools_used": report["tools_used"],
                    "evidence": report["evidence"],
                    "verification": ("independent" if result.task_verified
                                     else "agent_reported"),
                }}
            return {"status": "failed", "result": {
                "reason": "verification_failed", "summary": report["summary"],
            }}
        return {"status": "failed", "result": {
            "reason": "manager_failed", "summary": report["summary"],
            "evidence": report["evidence"],
        }}

    return run


def main(argv: list[str] | None = None) -> int:
    """Run once by default, or poll on an interval with ``--interval``."""
    parser = argparse.ArgumentParser(description="JARVIS PC harness connector")
    parser.add_argument("--config", type=Path, required=True,
                        help="trusted local JSON with manager_kind and driver settings")
    parser.add_argument("--base-url", default=os.getenv("JARVIS_CONTROL_URL"))
    parser.add_argument("--journal", type=Path,
                        default=Path("~/.local/state/jarvis/pc-launches.sqlite3"))
    parser.add_argument("--interval", type=float, default=0,
                        help="seconds between polls; 0 means one shot")
    args = parser.parse_args(argv)
    if not args.base_url or args.interval < 0:
        parser.error("base URL is required and interval cannot be negative")
    try:
        config = json.loads(args.config.expanduser().read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("config must be a JSON object")
        manager_kind = config.get("manager_kind")
        if not isinstance(manager_kind, str) or not manager_kind.strip():
            raise ValueError("config.manager_kind must be nonempty")
        driver = config.get("driver", "terminal")
        if driver == "terminal":
            manager = make_terminal_manager(config)
            reconcile_manager = None
        elif driver == "hermes_runs":
            if manager_kind != "hermes":
                raise ValueError("hermes_runs driver requires manager_kind=hermes")
            from .hermes_runs_manager import make_hermes_runs_manager
            task_verifier = make_task_verifier(config)
            completion_policy = config.get("completion_policy", "verified")
            if completion_policy not in ("verified", "agent_reported"):
                raise ValueError("config.completion_policy is invalid")
            manager = make_hermes_runs_manager(
                base_url=config.get("hermes_url", "http://127.0.0.1:8642"),
                verify=(lambda task, _status, _report: task_verifier(task))
                if task_verifier is not None else None,
                allow_agent_report=completion_policy == "agent_reported",
                poll_interval=config.get("poll_interval_seconds", 2.0),
                max_wait_seconds=config.get("max_wait_seconds", 600.0),
                request_timeout=config.get("request_timeout_seconds", 15.0),
            )
            reconcile_manager = manager
        else:
            raise ValueError("config.driver is invalid")
        while True:
            report = poll_once(base_url=args.base_url, manager_kind=manager_kind,
                               journal_path=args.journal, run_manager=manager,
                               reconcile_manager=reconcile_manager)
            print(json.dumps(report.__dict__, separators=(",", ":")))
            if not args.interval:
                return 0
            time.sleep(args.interval)
    except (OSError, ValueError, ConnectorError) as exc:
        # No task payload or token is printed by errors from this module.
        parser.exit(1, f"pc connector: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
