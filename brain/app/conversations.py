"""Conversation summary store (Katman 2b) — Firestore `conversations` collection.

Firestore cannot GROUP BY, so enumerating a user's conversations by scanning
`messages` would be an unbounded read over every message the user has ever
sent. Instead we keep one small summary doc per (user_id, session_id),
upserted alongside every messages.append() call (see main.run_turn, which
calls ConversationStore.touch() right after each MessageStore.append()).
`messages` stays append-only transcript history; `conversations` is the index
that makes "list my conversations" and "reopen one" possible without scanning
it.

NOT atomic with the messages write: Firestore has no cross-collection (or
even same-collection multi-doc) transaction in use here, so a crash between
MessageStore.append() and ConversationStore.touch() -- or between the two
deletes in main.delete_conversation -- can leave the two collections briefly
inconsistent (a message with no/stale summary row, or, on delete, a summary
gone with a stray message left behind or vice versa). Accepted for the same
reason messages.py accepts ts-tie risk: there is no concurrent-writer path
today for a single (user_id, session_id), so the window is real but narrow,
and a later message on the same session self-heals the summary side via
touch()'s upsert.
"""
from datetime import datetime, timezone
from typing import Callable

from google.cloud.firestore_v1 import Query
from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "conversations"
TITLE_MAX_LEN = 60        # sane length for a conversation-list row title
MAX_CONVERSATIONS = 50    # bounded read, same reasoning as messages.MAX_HISTORY_MESSAGES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_id(user_id: str, session_id: str) -> str:
    """Deterministic per-conversation key, scoped by user: makes cross-user
    document-id collisions on the same session_id structurally impossible,
    which is what keeps delete() safely user-keyed with no extra check."""
    return f"{user_id}::{session_id}"


def derive_title(text: str, max_len: int = TITLE_MAX_LEN) -> str:
    """Title from a user message: collapse all whitespace/newlines to single
    spaces, then cap at `max_len` chars with a trailing ellipsis if
    truncated. No LLM call -- deliberately simple (spec)."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


class ConversationStore:
    def __init__(self, db, now_fn: Callable[[], str] | None = None):
        self.db = db
        self._now = now_fn or _now

    def touch(self, user_id: str, session_id: str, role: str, text: str) -> None:
        """Upsert the summary doc for (user_id, session_id). Called once per
        MessageStore.append() (see main.run_turn), for both the user turn and
        the model turn.

        Title rule: derived ONLY from a "user" role message, and ONLY if not
        already set -- the first user message wins the title, permanently.
        A doc created by a non-"user" role first (should not happen given
        run_turn's ordering, but guarded anyway since this method's contract
        doesn't otherwise forbid it) gets an empty title that a later user
        message on the same session_id can still fill in, without ever
        overwriting a title that's already set.
        """
        doc_ref = self.db.collection(COLLECTION).document(_doc_id(user_id, session_id))
        snap = doc_ref.get()
        now = self._now()
        if not snap.exists:
            doc_ref.set({
                "user_id": user_id,
                "session_id": session_id,
                "title": derive_title(text) if role == "user" else "",
                "created_ts": now,
                "last_ts": now,
                "message_count": 1,
            })
            return
        data = snap.to_dict()
        update = {"last_ts": now, "message_count": data.get("message_count", 0) + 1}
        if role == "user" and not data.get("title"):
            update["title"] = derive_title(text)
        doc_ref.set(update, merge=True)

    def list_conversations(self, user_id: str, limit: int = MAX_CONVERSATIONS) -> list[dict]:
        """Newest-first (by last_ts) conversations for this user, bounded to
        `limit` -- an unbounded read here would repeat the exact mistake
        (scanning `messages`) this whole feature exists to avoid.

        Backward compatibility: conversations created before this feature
        shipped have no summary doc and will NOT appear here until their
        session_id receives a new message (which calls touch() and creates
        the doc then). Deliberate choice over a backfill-on-read scan of
        `messages`, which would just reintroduce the unbounded-read problem
        this collection exists to avoid -- see the task report for the full
        reasoning.
        """
        query = (
            self.db.collection(COLLECTION)
            .where(filter=FieldFilter("user_id", "==", user_id))
            .order_by("last_ts", direction=Query.DESCENDING)
            .limit(limit)
        )
        rows = [snap.to_dict() for snap in query.stream()]
        return [
            {
                "session_id": r["session_id"],
                "title": r["title"],
                "last_ts": r["last_ts"],
                "message_count": r["message_count"],
            }
            for r in rows
        ]

    def delete(self, user_id: str, session_id: str) -> None:
        """Delete this user's conversation summary doc. The doc id itself is
        user-scoped (_doc_id), so this can never touch another user's
        conversation even if session_id collides."""
        self.db.collection(COLLECTION).document(_doc_id(user_id, session_id)).delete()
