"""Idempotent Google Chat result delivery for the thin control plane.

The caller decides when a verified task result is ready and persists delivery
state. This module only performs one app-authenticated Chat API request.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import google.auth
from google.auth.transport.requests import AuthorizedSession


_SPACE = re.compile(r"spaces/[^/]+\Z")
_CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"


def _session():
    credentials, _ = google.auth.default(scopes=[_CHAT_BOT_SCOPE])
    return AuthorizedSession(credentials)


def send_task_result(record: dict, text: str, *, session: Any = None) -> dict:
    """Post one final message using a stable request ID for safe retries.

    Chat's ``requestId`` prevents duplicate messages when a response is lost.
    The caller must retry with the same task ID, text and app credentials.
    """
    if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
        raise ValueError("task record needs task_id")
    task_id = record["task_id"]
    if not task_id:
        raise ValueError("task_id must be nonempty")
    target = record.get("delivery_target")
    if not isinstance(target, dict) or target.get("channel") != "google_chat":
        raise ValueError("task is not addressed to Google Chat")
    space = target.get("space")
    if not isinstance(space, str) or _SPACE.fullmatch(space) is None:
        raise ValueError("invalid Google Chat space")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("result text must be nonempty")
    if len(text.encode("utf-8")) > 30_000:
        raise ValueError("result text exceeds the Chat message limit")

    body: dict[str, Any] = {"text": text}
    params = {"requestId": str(uuid5(NAMESPACE_URL, f"jarvis:chat:result:{task_id}"))}
    thread = target.get("thread")
    if target.get("space_type") == "SPACE" and thread is not None:
        if (not isinstance(thread, str)
                or re.fullmatch(re.escape(space) + r"/threads/[^/]+", thread) is None):
            raise ValueError("invalid Google Chat thread")
        body["thread"] = {"name": thread}
        params["messageReplyOption"] = "REPLY_MESSAGE_OR_FAIL"

    response = (session or _session()).post(
        f"https://chat.googleapis.com/v1/{space}/messages",
        params=params, json=body, timeout=15,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Google Chat message delivery failed (HTTP {response.status_code})")
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("name"), str):
        raise RuntimeError("Google Chat returned an invalid message identity")
    return result
