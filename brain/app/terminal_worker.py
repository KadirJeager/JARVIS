"""Run a selected manager harness through a bounded, CLI-neutral terminal turn.

The caller supplies an argv template and an independent task verifier.  This
module does not decide which harness to use or interpret its output format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import string
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence


_MAX_PROMPT_BYTES = 64 * 1024
_DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_FIELDS = frozenset({"workdir", "prompt_file", "result_file"})
_MODEL_AUTH_ENV = frozenset({
    "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_OPENAI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_TOKEN", "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN", "OPENAI_BASE_URL",
    # These authenticate the PC connector/control API, never a child harness.
    "JARVIS_WORKER_TOKEN", "JARVIS_INGEST_TOKEN",
})


class TerminalWorkerError(RuntimeError):
    """A short failure reason without the prompt, command output, or secrets."""


@dataclass(frozen=True)
class TerminalEvidence:
    """Raw bounded evidence; its text is intentionally excluded from repr."""

    exit_code: int
    stdout: str = field(repr=False)
    stderr: str = field(repr=False)
    result_file: str | None = field(repr=False)
    result_payload: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TerminalTaskResult:
    evidence: TerminalEvidence = field(repr=False)
    task_verified: bool


def _safe_environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    environment = os.environ.copy()
    if extra:
        environment.update(extra)
    for name in tuple(environment):
        upper = name.upper()
        if upper in _MODEL_AUTH_ENV or upper.endswith(("_API_KEY", "_API_TOKEN")):
            environment.pop(name, None)
    return environment


def _expand_argv(argv: Sequence[str], values: Mapping[str, str]) -> list[str]:
    if not argv or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("argv must contain string arguments")
    formatter = string.Formatter()
    expanded: list[str] = []
    for arg in argv:
        for _, field_name, format_spec, conversion in formatter.parse(arg):
            if field_name is not None and (
                field_name not in _FIELDS or format_spec or conversion
            ):
                raise ValueError("argv uses an unsupported placeholder")
        expanded.append(arg.format_map(values))
    if not expanded[0]:
        raise ValueError("argv executable must be non-empty")
    return expanded


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    # A child may retain the stdout descriptor after the CLI parent exits.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _read_bounded(path: Path, maximum: int) -> str:
    try:
        if path.stat().st_size > maximum:
            raise TerminalWorkerError("CLI output exceeded the size limit")
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        raise TerminalWorkerError("CLI returned invalid output") from None


def _render_stdin_template(template: Any, prompt: str) -> Any:
    if isinstance(template, str):
        return prompt if template == "{prompt}" else template
    if isinstance(template, list):
        return [_render_stdin_template(value, prompt) for value in template]
    if isinstance(template, dict):
        return {key: _render_stdin_template(value, prompt)
                for key, value in template.items()}
    return template


def _has_prompt_placeholder(template: Any) -> bool:
    if template == "{prompt}":
        return True
    if isinstance(template, dict):
        return any(_has_prompt_placeholder(value) for value in template.values())
    if isinstance(template, list):
        return any(_has_prompt_placeholder(value) for value in template)
    return False


def _json_path(value: Any, path: Sequence[str]) -> Any:
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise TerminalWorkerError("CLI did not return a usable structured result")
        value = value[key]
    return value


def _result_selector(selector: Mapping[str, Any]) -> tuple[str, str, Sequence[str], Sequence[str], str]:
    try:
        event_field = selector["event_field"]
        event_value = selector["event_value"]
        payload_path = selector["payload_path"]
        status_path = selector["status_path"]
        success_value = selector["success_value"]
    except (KeyError, TypeError):
        raise ValueError("stdout_result selector is incomplete") from None
    if (not isinstance(event_field, str) or not event_field
            or not isinstance(event_value, str) or not event_value
            or not isinstance(payload_path, (list, tuple)) or not payload_path
            or not isinstance(status_path, (list, tuple)) or not status_path
            or not all(isinstance(key, str) and key for key in (*payload_path, *status_path))
            or not isinstance(success_value, str) or not success_value):
        raise ValueError("stdout_result selector is invalid")
    return event_field, event_value, payload_path, status_path, success_value


def _structured_result(stdout: str, selector: Mapping[str, Any]) -> str:
    """Extract exactly one selected NDJSON event, without trusting CLI exit code."""
    event_field, event_value, payload_path, status_path, success_value = _result_selector(selector)
    selected = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            raise TerminalWorkerError("CLI returned malformed NDJSON") from None
        if not isinstance(event, dict):
            raise TerminalWorkerError("CLI returned malformed NDJSON")
        if event.get(event_field) == event_value:
            selected.append(event)
    if len(selected) != 1:
        raise TerminalWorkerError("CLI did not return exactly one result event")
    if _json_path(selected[0], status_path) != success_value:
        raise TerminalWorkerError("CLI result status was unsuccessful")
    payload = _json_path(selected[0], payload_path)
    if not isinstance(payload, dict):
        raise TerminalWorkerError("CLI did not return a usable structured result")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _run_command(
    argv: Sequence[str], *, workdir: Path, environment: Mapping[str, str],
    stdin_path: Path | None, stdout_path: Path, stderr_path: Path,
    result_path: Path, timeout_seconds: float, max_output_bytes: int,
) -> TerminalEvidence:
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        source = stdin_path.open("rb") if stdin_path else None
        try:
            try:
                process = subprocess.Popen(
                    argv, cwd=workdir, env=environment,
                    stdin=source if source else subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, start_new_session=True,
                )
            except OSError:
                raise TerminalWorkerError("CLI could not start") from None
            deadline = time.monotonic() + timeout_seconds
            try:
                while process.poll() is None:
                    if sum(
                        path.stat().st_size for path in (stdout_path, stderr_path, result_path)
                        if path.exists()
                    ) > max_output_bytes:
                        raise TerminalWorkerError("CLI output exceeded the size limit")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TerminalWorkerError("CLI timed out")
                    try:
                        process.wait(timeout=min(0.1, remaining))
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                _kill_group(process)
        finally:
            if source:
                source.close()

    try:
        total_size = sum(
            path.stat().st_size for path in (stdout_path, stderr_path, result_path)
            if path.exists()
        )
    except OSError:
        raise TerminalWorkerError("CLI returned invalid output") from None
    if total_size > max_output_bytes:
        raise TerminalWorkerError("CLI output exceeded the size limit")
    return TerminalEvidence(
        exit_code=process.returncode,
        stdout=_read_bounded(stdout_path, max_output_bytes),
        stderr=_read_bounded(stderr_path, max_output_bytes),
        result_file=(
            _read_bounded(result_path, max_output_bytes)
            if result_path.exists() else None
        ),
    )


def run_terminal_task(
    prompt: str,
    workdir: Path,
    *,
    argv: Sequence[str],
    verify: Callable[[TerminalEvidence], bool] | None = None,
    preflight_argv: Sequence[str] | None = None,
    preflight_expected: str | None = None,
    stdin_prompt: bool = False,
    stdin_json_template: Mapping[str, Any] | None = None,
    stdout_result: Mapping[str, Any] | None = None,
    timeout_seconds: float = 120,
    preflight_timeout_seconds: float = 10,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    env: Mapping[str, str] | None = None,
) -> TerminalTaskResult:
    """Run a command template with optional subscription-login preflight.

    Supported placeholders are ``{workdir}``, ``{prompt_file}``, and
    ``{result_file}``.  The prompt is available in a private temporary file
    and can also be sent on stdin, optionally inside a JSON line. No shell is involved. A zero CLI exit only
    becomes a verified task when ``verify(evidence)`` returns true.
    """
    if os.name != "posix":
        raise TerminalWorkerError("CLI process isolation requires POSIX")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be non-empty")
    if len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
        raise ValueError("prompt exceeds the size limit")
    if timeout_seconds <= 0 or preflight_timeout_seconds <= 0 or max_output_bytes < 1:
        raise ValueError("timeouts and output limit must be positive")
    if (preflight_argv is None) != (preflight_expected is None):
        raise ValueError("preflight command and expected text must be supplied together")
    if preflight_expected is not None and not preflight_expected.strip():
        raise ValueError("preflight expected text must be non-empty")
    if stdin_json_template is not None:
        if stdin_prompt or not isinstance(stdin_json_template, Mapping) or not _has_prompt_placeholder(stdin_json_template):
            raise ValueError("stdin_json_template must contain {prompt} and exclude stdin_prompt")
        try:
            json.dumps(stdin_json_template)
        except (TypeError, ValueError):
            raise ValueError("stdin_json_template must be JSON") from None
    if stdout_result is not None and not isinstance(stdout_result, Mapping):
        raise ValueError("stdout_result must be an object")
    if stdout_result is not None:
        _result_selector(stdout_result)
    workdir = Path(workdir).resolve(strict=True)
    if not workdir.is_dir():
        raise ValueError("workdir must be a directory")
    environment = _safe_environment(env)

    with tempfile.TemporaryDirectory(prefix="jarvis-terminal-") as temp_dir:
        temporary = Path(temp_dir)
        prompt_path = temporary / "prompt.txt"
        result_path = temporary / "result.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_path.chmod(0o600)
        input_path = prompt_path
        if stdin_json_template is not None:
            input_path = temporary / "stdin.jsonl"
            rendered = _render_stdin_template(stdin_json_template, prompt)
            input_path.write_text(json.dumps(rendered, ensure_ascii=False) + "\n", encoding="utf-8")
            input_path.chmod(0o600)
        values = {
            "workdir": str(workdir),
            "prompt_file": str(prompt_path),
            "result_file": str(result_path),
        }
        command = _expand_argv(argv, values)
        if not stdin_prompt and stdin_json_template is None and str(prompt_path) not in " ".join(command):
            raise ValueError("argv must use prompt_file or stdin_prompt")
        if preflight_argv is not None:
            preflight = _run_command(
                _expand_argv(preflight_argv, values), workdir=workdir,
                environment=environment, stdin_path=None,
                stdout_path=temporary / "preflight.out",
                stderr_path=temporary / "preflight.err",
                result_path=temporary / "preflight.result",
                timeout_seconds=preflight_timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
            output = preflight.stdout + "\n" + preflight.stderr
            if preflight.exit_code != 0 or preflight_expected.casefold() not in output.casefold():
                raise TerminalWorkerError("CLI preflight did not pass")
        evidence = _run_command(
            command, workdir=workdir, environment=environment,
            stdin_path=input_path if stdin_prompt or stdin_json_template is not None else None,
            stdout_path=temporary / "stdout.txt",
            stderr_path=temporary / "stderr.txt",
            result_path=result_path, timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        if evidence.exit_code != 0:
            raise TerminalWorkerError("CLI exited unsuccessfully")
        if stdout_result is not None:
            from dataclasses import replace
            evidence = replace(evidence, result_payload=_structured_result(evidence.stdout, stdout_result))

    if verify is None:
        return TerminalTaskResult(evidence, False)
    try:
        verified = verify(evidence)
    except Exception:
        raise TerminalWorkerError("Task verification failed") from None
    if not isinstance(verified, bool):
        raise TerminalWorkerError("Task verifier must return bool")
    return TerminalTaskResult(evidence, verified)
