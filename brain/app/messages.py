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

    def append(self, user_id: str, session_id: str, role: str, text: str,
               kind: str | None = None, meta: dict | None = None) -> None:
        """Append one transcript row. `kind`/`meta` are OPTIONAL (Faz Y3, spec
        §7): an approval card rides the transcript as a model row with
        kind="approval" and meta={"approval_id": ...}.

        Both fields are written ONLY when non-empty. A plain row therefore keeps
        exactly the five columns it had before Y3 -- an empty `kind` would force
        every reader (old Android build included) to handle a third state
        ("present but blank") that carries no information."""
        row = {
            "user_id": user_id,
            "session_id": session_id,
            "role": role,
            "text": text,
            "ts": self._now(),
        }
        if kind:
            row["kind"] = kind
        if meta:
            row["meta"] = meta
        self.db.collection(COLLECTION).add(row)

    def upsert_row(self, user_id: str, session_id: str, row_id: str, role: str,
                   text: str, kind: str | None = None,
                   meta: dict | None = None) -> None:
        """Write ONE transcript row under a DETERMINISTIC document id (`row_id`):
        create it on first call, overwrite text/ts in place on every later one.

        This is the F9 primitive -- a live progress line must UPDATE itself
        tick after tick instead of appending a new row per tick (the measured
        spam risk that kept F9 open). Same row shape as [append]; the caller
        owns the id scheme and must make it unique per (user, session, thing)
        -- embedding user_id and session_id in row_id is the convention (see
        tasks.progress_row_id)."""
        row = {
            "user_id": user_id,
            "session_id": session_id,
            "role": role,
            "text": text,
            "ts": self._now(),
        }
        if kind:
            row["kind"] = kind
        if meta:
            row["meta"] = meta
        self.db.collection(COLLECTION).document(row_id).set(row)

    def delete_row(self, user_id: str, session_id: str, row_id: str) -> bool:
        """Delete the deterministic-id row written by [upsert_row]. Returns
        whether a row was actually there (False = already gone or never
        written -- both are success for the caller's cleanup purpose)."""
        ref = self.db.collection(COLLECTION).document(row_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True

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
        return [self._project(r) for r in rows]

    @staticmethod
    def _project(row: dict) -> dict:
        """Wire shape of one transcript row.

        `kind`/`meta` appear only when the stored row carries them, so a row
        written before Y3 -- and every plain row written after it -- projects to
        the exact same three keys it always did. This is the backward-compat
        boundary the Android tolerant-wire reader leans on (spec §7)."""
        out = {"role": row["role"], "text": row["text"], "ts": row["ts"]}
        if row.get("kind"):
            out["kind"] = row["kind"]
        if row.get("meta"):
            out["meta"] = row["meta"]
        return out

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
