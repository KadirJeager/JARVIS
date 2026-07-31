"""Workspace pollers (Faz Y2.2/Y2.3): Calendar and Gmail, repo-watch pattern.

Baseline rule (same as repo_watch): the FIRST run records current state and
produces NO events -- otherwise the very first poll would flood the chat with
every existing calendar entry and unread mail. Later runs are incremental:
Calendar via syncToken, Gmail via historyId, both persisted in the
`workspace_state` collection (docs `calendar` and `gmail`).

Every new item is recorded through events.record() IN-PROCESS (the poller
runs inside brain; no HTTP hop to /api/jobs/event). Per-source error
isolation: if Calendar dies, Gmail still polls and the summary says which
half failed. DATA-level logging throughout (fetched -> recorded -> notified).
"""
import logging
from datetime import datetime, timedelta, timezone

from . import events, workspace

CALENDAR_STATE_DOC = "calendar"
GMAIL_STATE_DOC = "gmail"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
# How far ahead a NEW event must start to be worth an immediate ping. Inside
# this window a new calendar entry is schedule-relevant now, not "someday".
NOTIFY_WINDOW = timedelta(hours=24)


class HttpError(Exception):
    """Transport failure from the authorized GET helper; carries .status when
    the failure was an HTTP response (410 Gone means the syncToken died)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def authorized_get(url: str, creds, timeout: int = 30) -> dict:
    """GET a Google API endpoint with the workspace access token.

    Kept deliberately small (urllib, no google-api-client dep): the pollers
    only read lists. Raises HttpError on non-200 so callers can special-case
    410 (Calendar syncToken expiry). NOTE: `url` must already be encoded --
    see _qs() (a raw "+00:00" in timeMin became a space and cost a live 400).
    """
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise HttpError(f"HTTP {exc.code} for {url}", status=exc.code) from exc


def _qs(params: dict) -> str:
    """urlencode a query dict. A bare `+00:00` offset in timeMin is a SPACE in
    a query string -- Google's 400 for the calendar baseline came from exactly
    this (live, jarvis-brain-00023-wkq)."""
    from urllib.parse import urlencode

    return urlencode(params)


def _iso_now(now_fn) -> str:
    return now_fn().isoformat()


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state(db, doc_id: str) -> dict:
    snap = db.collection("workspace_state").document(doc_id).get()
    return snap.to_dict() if snap.exists else {}


def _save_state(db, doc_id: str, state: dict) -> None:
    db.collection("workspace_state").document(doc_id).set(state, merge=True)


def poll_calendar(db, now_fn=_default_now, get=authorized_get) -> dict:
    """Incremental Calendar poll. Returns a summary dict; never raises for
    API/config failures (those become handled:false observations, İlke 4)."""
    try:
        creds = workspace.workspace_credentials()
    except workspace.WorkspaceNotConfigured as exc:
        logging.info("calendar poll skipped: %s", exc)
        return {"handled": False, "reason": "workspace yapılandırılmamış"}

    state = _load_state(db, CALENDAR_STATE_DOC)
    sync_token = state.get("sync_token")
    now = now_fn()

    if not sync_token:
        # BASELINE: snapshot "from now on" and record nothing as an event.
        time_min = now.isoformat()
        page_token, events_seen = None, 0
        while True:
            params = {"timeMin": time_min, "singleEvents": "true", "maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            data = get(f"{CALENDAR_API}?{_qs(params)}", creds)
            events_seen += len(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                sync_token = data.get("nextSyncToken")
                break
        _save_state(db, CALENDAR_STATE_DOC, {"sync_token": sync_token, "baseline_at": _iso_now(now_fn)})
        logging.info("calendar baseline: %d upcoming events snapshotted, no events recorded", events_seen)
        return {"handled": True, "baseline": True, "seen": events_seen, "recorded": 0, "notified": 0}

    recorded = notified = 0
    try:
        page_token = None
        while True:
            params = {"syncToken": sync_token, "maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            data = get(f"{CALENDAR_API}?{_qs(params)}", creds)
            for item in data.get("items", []):
                recorded += 1
                start = item.get("start", {})
                start_raw = start.get("dateTime") or start.get("date")
                notify = False
                if item.get("status") != "cancelled" and start_raw:
                    try:
                        start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=timezone.utc)
                        notify = now <= start_dt <= now + NOTIFY_WINDOW
                    except ValueError:
                        notify = False
                if notify:
                    notified += 1
                events.record(
                    db,
                    source="calendar",
                    kind="calendar_event",
                    payload={
                        "id": item.get("id"),
                        "summary": item.get("summary", ""),
                        "start": start_raw,
                        "end": (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"),
                        "status": item.get("status"),
                        "notify": notify,
                    },
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                _save_state(db, CALENDAR_STATE_DOC, {"sync_token": data.get("nextSyncToken")})
                break
    except HttpError as exc:
        if exc.status == 410:
            # syncToken died (Google expires them). Clear and let the NEXT poll
            # take a fresh baseline -- losing it here would resync the same way
            # but mask the failure as a crash instead of an observation.
            logging.warning("calendar syncToken expired (410) -- clearing for fresh baseline next run")
            _save_state(db, CALENDAR_STATE_DOC, {"sync_token": None, "sync_lost_at": _iso_now(now_fn)})
            return {"handled": False, "reason": "syncToken eskidi, sonraki turda baseline yenilenecek"}
        raise
    logging.info("calendar poll: recorded=%d notified=%d", recorded, notified)
    return {"handled": True, "baseline": False, "recorded": recorded, "notified": notified}


def poll_gmail(db, now_fn=_default_now, get=authorized_get) -> dict:
    """Incremental Gmail poll over unread inbox. Important = is:important or
    is:starred (simple rule this phase; LLM triage is a later phase -- see
    module docstring note in the plan)."""
    try:
        creds = workspace.workspace_credentials()
    except workspace.WorkspaceNotConfigured as exc:
        logging.info("gmail poll skipped: %s", exc)
        return {"handled": False, "reason": "workspace yapılandırılmamış"}

    state = _load_state(db, GMAIL_STATE_DOC)
    last_history = state.get("last_history_id")

    profile = get(f"{GMAIL_API}/profile", creds)
    current_history = profile.get("historyId")

    if not last_history:
        _save_state(db, GMAIL_STATE_DOC, {"last_history_id": current_history, "baseline_at": _iso_now(now_fn)})
        logging.info("gmail baseline: historyId=%s recorded, no mails processed", current_history)
        return {"handled": True, "baseline": True, "recorded": 0, "notified": 0}

    recorded = notified = 0
    data = get(f"{GMAIL_API}/messages?{_qs({'q': 'is:unread -in:chats', 'maxResults': 50})}", creds)
    for stub in data.get("messages", []):
        msg_id = stub["id"]
        if last_history and msg_id <= last_history:
            continue  # already seen in a previous poll (ids are monotonic per mailbox)
        msg = get(
            f"{GMAIL_API}/messages/{msg_id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject",
            creds,
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        labels = set(msg.get("labelIds", []))
        important = "IMPORTANT" in labels or "STARRED" in labels
        recorded += 1
        if important:
            notified += 1
        events.record(
            db,
            source="gmail",
            kind="gmail_message",
            payload={
                "id": msg_id,
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "important": important,
                "notify": important,
            },
        )
    _save_state(db, GMAIL_STATE_DOC, {"last_history_id": current_history})
    logging.info("gmail poll: recorded=%d notified=%d", recorded, notified)
    return {"handled": True, "baseline": False, "recorded": recorded, "notified": notified}


def poll_all(db, now_fn=_default_now, get=authorized_get) -> dict:
    """Run both pollers with per-source error isolation (repo-watch rule:
    one dying source must not kill the other)."""
    summary: dict = {"calendar": None, "gmail": None}
    for name, poller in (("calendar", poll_calendar), ("gmail", poll_gmail)):
        try:
            summary[name] = poller(db, now_fn=now_fn, get=get)
        except Exception:
            logging.exception("workspace poll: %s failed", name)
            summary[name] = {"handled": False, "reason": f"{name} poller hatası (loglara bak)"}
    summary["notify"] = any(
        isinstance(summary[name], dict) and summary[name].get("notified", 0) > 0
        for name in ("calendar", "gmail")
    )
    return summary
