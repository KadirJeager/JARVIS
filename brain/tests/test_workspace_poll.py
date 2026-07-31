"""Tests for app/workspace_poll.py — transport fully stubbed, no real Google calls."""
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import events, workspace_poll
from app.workspace import WorkspaceNotConfigured
from tests.fakes import FakeDB

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


def _now():
    return NOW


def _creds():
    class C:
        token = "fake-access-token"

    return C()


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch):
    monkeypatch.setattr(
        "app.workspace.workspace_credentials", lambda db=None: _creds()
    )


def _msg(msg_id, subject="s", sender="a@b.c", labels=None):
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ]
        },
        "labelIds": labels or [],
    }


class StubGet:
    """URL-routed stub for authorized_get."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, creds, timeout=30):
        self.calls.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response(url) if callable(response) else response
        raise AssertionError(f"unstubbed URL: {url}")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def test_calendar_baseline_records_nothing_and_saves_sync_token():
    db = FakeDB()
    stub = StubGet({
        workspace_poll.CALENDAR_API: lambda url: {
            "items": [{"id": "e1"}, {"id": "e2"}],
            "nextSyncToken": "tok-1",
        }
    })
    out = workspace_poll.poll_calendar(db, now_fn=_now, get=stub)
    assert out["baseline"] is True and out["seen"] == 2 and out["recorded"] == 0
    state = db.collection("workspace_state").document("calendar").get().to_dict()
    assert state["sync_token"] == "tok-1"
    # No events were recorded for existing items
    assert not any(True for _ in db.collection("events").stream())


def test_calendar_incremental_records_new_events_and_notify_window():
    db = FakeDB()
    db.collection("workspace_state").document("calendar").set({"sync_token": "tok-1"})
    inside = (NOW + timedelta(hours=2)).isoformat()
    outside = (NOW + timedelta(days=3)).isoformat()
    stub = StubGet({
        workspace_poll.CALENDAR_API: lambda url: {
            "items": [
                {"id": "a", "summary": "yakın", "status": "confirmed",
                 "start": {"dateTime": inside}, "end": {"dateTime": inside}},
                {"id": "b", "summary": "uzak", "status": "confirmed",
                 "start": {"dateTime": outside}, "end": {"dateTime": outside}},
                {"id": "c", "summary": "iptal", "status": "cancelled",
                 "start": {"dateTime": inside}, "end": {"dateTime": inside}},
            ],
            "nextSyncToken": "tok-2",
        }
    })
    out = workspace_poll.poll_calendar(db, now_fn=_now, get=stub)
    assert out["recorded"] == 3 and out["notified"] == 1
    state = db.collection("workspace_state").document("calendar").get().to_dict()
    assert state["sync_token"] == "tok-2"
    recorded = [s.to_dict() for s in db.collection("events").stream()]
    assert {r["payload"]["summary"] for r in recorded} == {"yakın", "uzak", "iptal"}
    notify_flags = {r["payload"]["summary"]: r["result"]["notify"] for r in recorded}
    assert notify_flags == {"yakın": True, "uzak": False, "iptal": False}


def test_calendar_410_clears_sync_token_for_fresh_baseline():
    db = FakeDB()
    db.collection("workspace_state").document("calendar").set({"sync_token": "dead"})
    stub = StubGet({
        workspace_poll.CALENDAR_API: workspace_poll.HttpError("gone", status=410),
    })
    out = workspace_poll.poll_calendar(db, now_fn=_now, get=stub)
    assert out["handled"] is False
    state = db.collection("workspace_state").document("calendar").get().to_dict()
    assert state["sync_token"] is None


def test_calendar_not_configured_is_silent():
    db = FakeDB()
    with patch("app.workspace.workspace_credentials", side_effect=WorkspaceNotConfigured("yok")):
        out = workspace_poll.poll_calendar(db, now_fn=_now, get=StubGet({}))
    assert out == {"handled": False, "reason": "workspace yapılandırılmamış"}


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


def test_gmail_baseline_only_saves_history_id():
    db = FakeDB()
    stub = StubGet({
        f"{workspace_poll.GMAIL_API}/profile": {"historyId": "5000"},
    })
    out = workspace_poll.poll_gmail(db, now_fn=_now, get=stub)
    assert out["baseline"] is True and out["recorded"] == 0
    state = db.collection("workspace_state").document("gmail").get().to_dict()
    assert state["last_history_id"] == "5000"


def test_gmail_incremental_records_only_new_and_important_notifies():
    db = FakeDB()
    db.collection("workspace_state").document("gmail").set({"last_history_id": "100"})
    stub = StubGet({
        f"{workspace_poll.GMAIL_API}/profile": {"historyId": "300"},
        f"{workspace_poll.GMAIL_API}/messages?q=": {
            "messages": [{"id": "050"}, {"id": "150"}, {"id": "250"}]
        },
        f"{workspace_poll.GMAIL_API}/messages/050": _msg("050", labels=["IMPORTANT"]),
        f"{workspace_poll.GMAIL_API}/messages/150": _msg("150", subject="önemsiz"),
        f"{workspace_poll.GMAIL_API}/messages/250": _msg("250", labels=["STARRED"]),
    })
    out = workspace_poll.poll_gmail(db, now_fn=_now, get=stub)
    # '050' > '100' is False (string compare), so it is skipped as already seen;
    # '150' and '250' are > '100' and get recorded.
    assert out["recorded"] == 2
    recorded = [s.to_dict() for s in db.collection("events").stream()]
    ids = {r["payload"]["id"] for r in recorded}
    assert ids == {"150", "250"}
    state = db.collection("workspace_state").document("gmail").get().to_dict()
    assert state["last_history_id"] == "300"


def test_gmail_important_rule():
    db = FakeDB()
    db.collection("workspace_state").document("gmail").set({"last_history_id": "000"})
    stub = StubGet({
        f"{workspace_poll.GMAIL_API}/profile": {"historyId": "300"},
        f"{workspace_poll.GMAIL_API}/messages?q=": {"messages": [{"id": "150"}, {"id": "250"}]},
        f"{workspace_poll.GMAIL_API}/messages/150": _msg("150", labels=["IMPORTANT"]),
        f"{workspace_poll.GMAIL_API}/messages/250": _msg("250"),
    })
    out = workspace_poll.poll_gmail(db, now_fn=_now, get=stub)
    assert out["notified"] == 1
    recorded = {s.to_dict()["payload"]["id"]: s.to_dict()["result"]["notify"]
                for s in db.collection("events").stream()}
    assert recorded == {"150": True, "250": False}


def test_gmail_not_configured_is_silent():
    db = FakeDB()
    with patch("app.workspace.workspace_credentials", side_effect=WorkspaceNotConfigured("yok")):
        out = workspace_poll.poll_gmail(db, now_fn=_now, get=StubGet({}))
    assert out == {"handled": False, "reason": "workspace yapılandırılmamış"}


# ---------------------------------------------------------------------------
# poll_all isolation
# ---------------------------------------------------------------------------


def test_poll_all_isolates_failing_source():
    db = FakeDB()

    def boom(db, now_fn, get):
        raise RuntimeError("calendar dead")

    with patch.object(workspace_poll, "poll_calendar", side_effect=boom), \
         patch.object(workspace_poll, "poll_gmail", return_value={"handled": True, "recorded": 1, "notified": 1}):
        out = workspace_poll.poll_all(db, now_fn=_now, get=StubGet({}))
    assert out["calendar"]["handled"] is False
    assert out["gmail"]["handled"] is True
    assert out["notify"] is True
