"""Synthetic terminal harness tests; no model or account is contacted."""

import json
import os
from pathlib import Path
import sys
import time

import pytest

from app.terminal_worker import TerminalWorkerError, run_terminal_task


@pytest.fixture
def fake_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import os, pathlib, sys, time\n"
        "mode = sys.argv[1]\n"
        "if mode == 'preflight':\n"
        "    print(os.getenv('FAKE_LOGIN', 'Logged in using ChatGPT'))\n"
        "    sys.exit(int(os.getenv('FAKE_LOGIN_EXIT', '0')))\n"
        "if mode == 'hang':\n"
        "    pathlib.Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "if mode == 'child':\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        time.sleep(60)\n"
        "        os._exit(0)\n"
        "    pathlib.Path(os.environ['FAKE_PID_FILE']).write_text(str(pid))\n"
        "if mode == 'large':\n"
        "    print('X' * 20000, flush=True)\n"
        "    time.sleep(60)\n"
        "if mode == 'run':\n"
        "    prompt = pathlib.Path(sys.argv[2]).read_text()\n"
        "    pathlib.Path(sys.argv[3]).write_text('FILE:' + prompt)\n"
        "    print('OUT:' + prompt)\n"
        "    print('ERR:hello', file=sys.stderr)\n"
        "    print('API_KEY=' + str(os.getenv('OPENAI_API_KEY')))\n"
        "    print('GEMINI_KEY=' + str(os.getenv('GEMINI_API_KEY')))\n"
        "    print('CUSTOM_KEY=' + str(os.getenv('CUSTOM_API_KEY')))\n"
        "    print('WORKER_TOKEN=' + str(os.getenv('JARVIS_WORKER_TOKEN')))\n"
        "    print('INGEST_TOKEN=' + str(os.getenv('JARVIS_INGEST_TOKEN')))\n"
        "    print('WORKDIR=' + sys.argv[4])\n"
        "    sys.exit(int(os.getenv('FAKE_EXIT', '0')))\n"
        "if mode == 'stdin':\n"
        "    print('STDIN:' + sys.stdin.read())\n"
        "if mode == 'json-stream':\n"
        "    event = __import__('json').loads(sys.stdin.readline())\n"
        "    content = event['message']['content']\n"
        "    print(__import__('json').dumps({'event':'init'}))\n"
        "    if os.getenv('FAKE_STREAM_MALFORMED'):\n"
        "        print('{broken')\n"
        "    if not os.getenv('FAKE_STREAM_MISSING'):\n"
        "        payload = {'status':'completed','summary':content,'tools_used':[],"
        "                   'evidence':[],'next_action':''}\n"
        "        print(__import__('json').dumps({'event':'result','result':"
        "              {'status':os.getenv('FAKE_STREAM_STATUS','SUCCESS'),"
        "               'structured_output':payload}}))\n"
    )
    return script


def _argv(fake_cli: Path) -> tuple[str, ...]:
    return (sys.executable, str(fake_cli), "run", "{prompt_file}", "{result_file}", "{workdir}")


def _preflight(fake_cli: Path) -> tuple[str, ...]:
    return (sys.executable, str(fake_cli), "preflight")


def test_template_preflight_evidence_and_independent_verifier(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini")
    monkeypatch.setenv("JARVIS_WORKER_TOKEN", "secret-worker")
    monkeypatch.setenv("JARVIS_INGEST_TOKEN", "secret-ingest")
    prompt = "Please do task $(touch should-not-exist)"
    result = run_terminal_task(
        prompt, tmp_path, argv=_argv(fake_cli),
        preflight_argv=_preflight(fake_cli),
        preflight_expected="logged in using chatgpt",
        env={"CUSTOM_API_KEY": "secret-custom"},
        verify=lambda evidence: evidence.result_file == "FILE:" + prompt
        and evidence.stdout.startswith("OUT:" + prompt),
    )
    assert result.task_verified is True
    assert result.evidence.exit_code == 0
    assert "API_KEY=None" in result.evidence.stdout
    assert "GEMINI_KEY=None" in result.evidence.stdout
    assert "CUSTOM_KEY=None" in result.evidence.stdout
    assert "WORKER_TOKEN=None" in result.evidence.stdout
    assert "INGEST_TOKEN=None" in result.evidence.stdout
    assert f"WORKDIR={tmp_path}" in result.evidence.stdout
    assert "ERR:hello" in result.evidence.stderr
    assert prompt not in repr(result)
    assert not (tmp_path / "should-not-exist").exists()


def test_successful_command_is_unverified_without_verifier(fake_cli, tmp_path):
    result = run_terminal_task("hello", tmp_path, argv=_argv(fake_cli))
    assert result.evidence.result_file == "FILE:hello"
    assert result.task_verified is False


def test_stdin_prompt_without_cli_specific_parser(fake_cli, tmp_path):
    result = run_terminal_task(
        "stdin body", tmp_path,
        argv=(sys.executable, str(fake_cli), "stdin"), stdin_prompt=True,
        verify=lambda evidence: "STDIN:stdin body" in evidence.stdout,
    )
    assert result.task_verified is True
    assert result.evidence.result_file is None


_STREAM_SELECTOR = {
    "event_field": "event", "event_value": "result",
    "payload_path": ["result", "structured_output"],
    "status_path": ["result", "status"], "success_value": "SUCCESS",
}


def test_json_stdin_and_ndjson_result_without_file(fake_cli, tmp_path):
    prompt = "private text with newline\nand {braces}"
    result = run_terminal_task(
        prompt, tmp_path, argv=(sys.executable, str(fake_cli), "json-stream"),
        stdin_json_template={"event": "user", "message": {"content": "{prompt}"}},
        stdout_result=_STREAM_SELECTOR,
        verify=lambda evidence: json.loads(evidence.result_payload)["summary"] == prompt,
    )
    assert result.task_verified
    assert result.evidence.result_file is None
    assert json.loads(result.evidence.result_payload)["summary"] == prompt
    assert prompt not in repr(result)


@pytest.mark.parametrize("mode,expected", [
    ("FAKE_STREAM_MALFORMED", "malformed NDJSON"),
    ("FAKE_STREAM_MISSING", "exactly one result event"),
    ("FAKE_STREAM_STATUS", "result status was unsuccessful"),
])
def test_json_stream_rejects_bad_or_missing_result(fake_cli, tmp_path, monkeypatch, mode, expected):
    monkeypatch.setenv(mode, "ERROR" if mode == "FAKE_STREAM_STATUS" else "1")
    with pytest.raises(TerminalWorkerError, match=expected):
        run_terminal_task(
            "private", tmp_path, argv=(sys.executable, str(fake_cli), "json-stream"),
            stdin_json_template={"event": "user", "message": {"content": "{prompt}"}},
            stdout_result=_STREAM_SELECTOR,
        )


@pytest.mark.parametrize("login,code", [("API key login", "0"), ("Logged in using ChatGPT", "9")])
def test_preflight_rejects_wrong_login_or_exit(fake_cli, tmp_path, monkeypatch, login, code):
    monkeypatch.setenv("FAKE_LOGIN", login)
    monkeypatch.setenv("FAKE_LOGIN_EXIT", code)
    with pytest.raises(TerminalWorkerError, match="preflight did not pass"):
        run_terminal_task(
            "hello", tmp_path, argv=_argv(fake_cli),
            preflight_argv=_preflight(fake_cli),
            preflight_expected="Logged in using ChatGPT",
        )


def test_nonzero_exit_and_verifier_error_are_sanitized(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "7")
    with pytest.raises(TerminalWorkerError, match="exited unsuccessfully") as caught:
        run_terminal_task("private prompt", tmp_path, argv=_argv(fake_cli))
    assert "private prompt" not in str(caught.value)
    monkeypatch.delenv("FAKE_EXIT")

    def broken(_evidence):
        raise RuntimeError("private verifier contents")

    with pytest.raises(TerminalWorkerError, match="Task verification failed") as caught:
        run_terminal_task("private prompt", tmp_path, argv=_argv(fake_cli), verify=broken)
    assert "private verifier contents" not in str(caught.value)


def test_timeout_reaps_process(fake_cli, tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    start = time.monotonic()
    with pytest.raises(TerminalWorkerError, match="timed out"):
        run_terminal_task(
            "hello", tmp_path, argv=(sys.executable, str(fake_cli), "hang"),
            stdin_prompt=True, timeout_seconds=0.4,
        )
    assert time.monotonic() - start < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="Linux process status is required")
def test_child_is_stopped_after_parent_exit(fake_cli, tmp_path, monkeypatch):
    pid_file = tmp_path / "child-pid"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    result = run_terminal_task(
        "hello", tmp_path, argv=(sys.executable, str(fake_cli), "child"),
        stdin_prompt=True,
    )
    assert result.task_verified is False
    child_pid = int(pid_file.read_text())
    try:
        for _ in range(20):
            stat = Path(f"/proc/{child_pid}/stat")
            if not stat.exists() or stat.read_text().split()[2] == "Z":
                break
            time.sleep(0.05)
        else:
            pytest.fail("CLI child remained running")
    finally:
        try:
            os.kill(child_pid, 9)
        except ProcessLookupError:
            pass


def test_output_limit_stops_command(fake_cli, tmp_path):
    start = time.monotonic()
    with pytest.raises(TerminalWorkerError, match="size limit"):
        run_terminal_task(
            "hello", tmp_path, argv=(sys.executable, str(fake_cli), "large"),
            stdin_prompt=True, timeout_seconds=5, max_output_bytes=1024,
        )
    assert time.monotonic() - start < 3


def test_rejects_unsupported_template_placeholder(fake_cli, tmp_path):
    with pytest.raises(ValueError, match="unsupported placeholder"):
        run_terminal_task(
            "hello", tmp_path,
            argv=(sys.executable, str(fake_cli), "run", "{unknown}"),
        )


def test_prompt_delivery_must_be_configured(fake_cli, tmp_path):
    with pytest.raises(ValueError, match="prompt_file or stdin_prompt"):
        run_terminal_task("hello", tmp_path, argv=(sys.executable, str(fake_cli), "stdin"))
