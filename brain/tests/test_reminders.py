"""Tests for app/reminders.py, app/fcm.py and their main.py endpoints."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import fcm, main as main_mod, reminders
from tests.fakes import FakeDB

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


def _now():
    return NOW.isoformat()


def _future(hours=2):
    return (NOW + timedelta(hours=hours)).isoformat()


def _past(hours=2):
    return (NOW - timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# reminders.set_reminder / list_reminders
# ---------------------------------------------------------------------------


def test_set_reminder_rejects_empty_text():
    assert reminders.set_reminder(FakeDB(), " ", _future()).startswith("Hata")


def test_set_reminder_rejects_bad_date():
    assert reminders.set_reminder(FakeDB(), "su iç", "yarin").startswith("Hata")


def test_set_reminder_rejects_past_date():
    assert reminders.set_reminder(FakeDB(), "su iç", _past(), now_fn=_now).startswith("Hata")


def test_set_reminder_writes_pending_doc():
    db = FakeDB()
    out = reminders.set_reminder(db, "su iç", _future(), now_fn=_now)
    assert "Hatırlatma kuruldu" in out and "su iç" in out
    docs = list(db.collection("reminders").stream())
    assert len(docs) == 1
    d = docs[0].to_dict()
    assert d["status"] == "pending" and d["text"] == "su iç"


def test_list_reminders_sorted_by_due_at():
    db = FakeDB()
    reminders.set_reminder(db, "geç", _future(1), now_fn=_now)
    reminders.set_reminder(db, "erken", _future(5), now_fn=_now)
    out = reminders.list_reminders(db)
    assert out["sayi"] == 2
    assert out["hatirlatmalar"][0]["due_at"] <= out["hatirlatmalar"][1]["due_at"]


# ---------------------------------------------------------------------------
# reminders.dispatch_due
# ---------------------------------------------------------------------------


def _seed(db, text, due_at, status="pending"):
    """dispatch testleri için doğrudan koleksiyona hatırlatma yazar
    (set_reminder geçmişi reddettiğinden 'due' vakası ancak böyle kurulur)."""
    db.collection("reminders").add({
        "text": text, "due_at": due_at, "status": status,
        "created_at": _now(), "sent_at": None, "fcm_result": None,
    })


def test_dispatch_due_sends_only_due_and_marks_sent():
    db = FakeDB()
    _seed(db, "zamanı geldi", _past(1))
    _seed(db, "daha erken", _future(5))
    sent = []
    out = reminders.dispatch_due(db, lambda _db, r: sent.append(r["text"]) or {"ok": True}, now_fn=_now)
    assert out == {"handled": True, "sent": 1, "failed": 0}
    assert sent == ["zamanı geldi"]
    docs = {s.to_dict()["text"]: s.to_dict()["status"] for s in db.collection("reminders").stream()}
    assert docs["zamanı geldi"] == "sent"
    assert docs["daha erken"] == "pending"


def test_dispatch_due_failure_keeps_pending_and_records_error():
    db = FakeDB()
    _seed(db, "patlar", _past(1))

    def fail(_db, _r):
        raise RuntimeError("fcm dead")

    out = reminders.dispatch_due(db, fail, now_fn=_now)
    assert out["sent"] == 0 and out["failed"] == 1
    doc = list(db.collection("reminders").stream())[0].to_dict()
    assert doc["status"] == "pending" and doc["fcm_result"]["ok"] is False


# ---------------------------------------------------------------------------
# fcm.register_token / send_reminder fallback
# ---------------------------------------------------------------------------


def test_register_token_rejects_blank():
    with pytest.raises(ValueError):
        fcm.register_token(FakeDB(), "u", "  ")


def test_register_token_upserts_by_hash():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")
    fcm.register_token(db, "u", "tok-1")
    docs = list(db.collection("fcm_tokens").stream())
    assert len(docs) == 1
    assert docs[0].to_dict()["token"] == "tok-1"


def test_send_reminder_without_tokens_falls_back_to_chat():
    db = FakeDB()
    with patch("app.tasks.default_owner", return_value="kadir@example.com"):
        out = fcm.send_reminder(db, {"id": "r1", "text": "su iç"})
    assert out["ok"] is True and out["fallback"] == "chat_reminders"
    msgs = list(db.collection("messages").stream())
    assert any("su iç" in str(m.to_dict().get("text", "")) for m in msgs)


def test_send_reminder_to_tokens_uses_injected_http():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")
    calls = []
    out = fcm.send_reminder(
        db, {"id": "r1", "text": "su iç"},
        send_http_fn=lambda url, payload, token: calls.append((url, payload, token)) or {"ok": True},
    )
    assert out["ok"] is True
    assert calls and calls[0][1]["message"]["token"] == "tok-1"


# ---------------------------------------------------------------------------
# main.py endpoints
# ---------------------------------------------------------------------------


def test_fcm_register_endpoint_requires_auth():
    from fastapi.testclient import TestClient

    client = TestClient(main_mod.app)
    resp = client.post("/api/fcm/register", json={"token": "x"})
    assert resp.status_code == 401


def test_reminders_tick_requires_scheduler():
    from fastapi.testclient import TestClient

    client = TestClient(main_mod.app)
    resp = client.post("/api/jobs/reminders-tick")
    assert resp.status_code in (401, 503)


def test_workspace_poll_requires_scheduler():
    from fastapi.testclient import TestClient

    client = TestClient(main_mod.app)
    resp = client.post("/api/jobs/workspace-poll")
    assert resp.status_code in (401, 503)
