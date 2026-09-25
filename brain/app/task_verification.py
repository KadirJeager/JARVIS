"""Small, CLI-neutral checks selected by trusted local PC configuration.

The task, manager output and model may describe success, but cannot choose a
check. Only the local configuration maps an exact immutable task ID to checks.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


_MAX_FILE_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _validated_check(check: Any) -> dict:
    if not isinstance(check, dict):
        raise ValueError("verification check must be an object")
    kind = check.get("kind")
    expected_fields = {
        "file_exists": {"kind", "path"},
        "file_equals": {"kind", "path", "text"},
        "file_sha256": {"kind", "path", "sha256"},
    }.get(kind)
    if expected_fields is None or set(check) != expected_fields:
        raise ValueError("verification check has invalid fields")
    path = check["path"]
    if (not isinstance(path, str) or not path or "\0" in path
            or Path(path).is_absolute() or any(part in ("", ".", "..")
                                                for part in path.split("/"))
            or "\\" in path):
        raise ValueError("verification path must be a relative file path")
    if kind == "file_equals" and (
        not isinstance(check["text"], str)
        or len(check["text"].encode("utf-8")) > _MAX_FILE_BYTES
    ):
        raise ValueError("verification text is invalid or too large")
    if kind == "file_sha256" and (
        not isinstance(check["sha256"], str)
        or _SHA256.fullmatch(check["sha256"]) is None
    ):
        raise ValueError("verification sha256 must be lowercase hex")
    return dict(check)


def _read_regular_file(root: Path, relative: str, *, existence_only: bool) -> bytes | None:
    """Open each path component without following symlinks under root."""
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        parts = relative.split("/")
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            descriptors.append(directory)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW,
                          dir_fd=directory)
        descriptors.append(file_fd)
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if existence_only:
            return b""
        if metadata.st_size > _MAX_FILE_BYTES:
            return None
        content = bytearray()
        while len(content) <= _MAX_FILE_BYTES:
            chunk = os.read(file_fd, min(64 * 1024, _MAX_FILE_BYTES + 1 - len(content)))
            if not chunk:
                return bytes(content)
            content.extend(chunk)
        return None
    except (OSError, ValueError):
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def make_task_verifier(config: Mapping[str, Any], *, default_root: str | None = None):
    """Return a task verifier or None if no local policy was configured.

    ``verification.by_task_id`` is local operator configuration; task envelopes
    and CLI reports never supply check types, paths, or expected values.
    """
    policy = config.get("verification")
    if policy is None:
        return None
    if not isinstance(policy, dict) or set(policy) - {"root", "by_task_id"}:
        raise ValueError("verification must contain root and by_task_id only")
    root_value = policy.get("root", default_root)
    if not isinstance(root_value, str) or not root_value.strip():
        raise ValueError("verification.root must be configured")
    root = Path(root_value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("verification.root must be a directory")
    rules = policy.get("by_task_id")
    if not isinstance(rules, dict):
        raise ValueError("verification.by_task_id must be an object")
    validated = {}
    for task_id, checks in rules.items():
        if (not isinstance(task_id, str) or not task_id.strip()
                or not isinstance(checks, list) or not checks):
            raise ValueError("verification requires nonempty task IDs and check lists")
        validated[task_id] = [_validated_check(check) for check in checks]

    def verify(task: Mapping[str, Any]) -> bool:
        task_id = task.get("task_id") if isinstance(task, Mapping) else None
        checks = validated.get(task_id) if isinstance(task_id, str) else None
        if not checks:
            return False
        for check in checks:
            content = _read_regular_file(root, check["path"],
                                         existence_only=check["kind"] == "file_exists")
            if content is None:
                return False
            if check["kind"] == "file_equals":
                if content != check["text"].encode("utf-8"):
                    return False
            elif check["kind"] == "file_sha256":
                if hashlib.sha256(content).hexdigest() != check["sha256"]:
                    return False
        return True

    return verify
