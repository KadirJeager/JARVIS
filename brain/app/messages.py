"""Persistent chat transcript store (Katman 2b) — Firestore `messages` collection.

Application-layer persistence, deliberately independent of ADK's session
backend (ADK 1.36.2 offers no Firestore-native SessionService; see spec §12).
Single responsibility: append turns and read them back, keyed by user+session.
"""
import re
from datetime import datetime, timezone
from typing import Callable

from google.cloud.firestore_v1 import Query
from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "messages"
MAX_HISTORY_MESSAGES = 100  # ~50 turns (1 turn = user + model)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_session_id(raw: str) -> str:
    """Return `raw` if it is a safe session id, else raise ValueError.

    Guards the client-supplied session_id before it reaches Firestore or gets
    used as a document key: allow only URL/id-safe chars, bounded length, no
    slashes/newlines/path tricks (spec §9 'session_id sanitization')."""
    if isinstance(raw, str) and _SESSION_ID_RE.fullmatch(raw):
        return raw
    raise ValueError("gecersiz session_id")


class MessageStore:
    def __init__(self, db, now_fn: Callable[[], str] | None = None):
        self.db = db
        self._now = now_fn or _now

    def append(self, user_id: str, session_id: str, role: str, text: str) -> None:
        self.db.collection(COLLECTION).add(
            {
                "user_id": user_id,
                "session_id": session_id,
                "role": role,
                "text": text,
                "ts": self._now(),
            }
        )

    def history(self, user_id: str, session_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
        """Newest `limit` messages for (user_id, session_id), returned oldest→newest.

        DESC + limit fetches the newest N (bounded read); we reverse to
        chronological so callers (UI render, rehydration replay) get natural
        order."""
        # ts-tie risk (identical microsecond timestamps) is accepted: real flow has
        # seconds of I/O between appends (user msg → LLM → model msg) so ties are rare;
        # test determinism comes from injected now_fn; monotonic seq was judged YAGNI.
        query = (
            self.db.collection(COLLECTION)
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("session_id", "==", session_id))
            .order_by("ts", direction=Query.DESCENDING)
            .limit(limit)
        )
        rows = [snap.to_dict() for snap in query.stream()]
        rows.reverse()
        return [{"role": r["role"], "text": r["text"], "ts": r["ts"]} for r in rows]

    def delete_session(self, user_id: str, session_id: str) -> int:
        """Delete every message row for (user_id, session_id). Returns the
        count deleted (for logging; callers don't need to act on it).

        Firestore has no `DELETE WHERE`: this queries for the matching rows
        and deletes each doc individually via its snapshot's `.reference`
        (the standard delete-by-query idiom). Not atomic with the caller's
        other write (conversations.ConversationStore.delete) -- see
        conversations.py's module docstring for the accepted failure mode."""
        query = (
            self.db.collection(COLLECTION)
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("session_id", "==", session_id))
        )
        count = 0
        for snap in query.stream():
            snap.reference.delete()
            count += 1
        return count
