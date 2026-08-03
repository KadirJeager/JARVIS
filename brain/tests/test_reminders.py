"""Tests for app/reminders.py, app/fcm.py and their main.py endpoints."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import fcm, main as main_mod, reminders
from app.reminders import (REMINDERS_COLLECTION, STATUS_CANCELLED, STATUS_PENDING,
                           cancel, dispatch_due, set_reminder)
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
    _, ref = db.collection("reminders").add({
        "text": text, "due_at": due_at, "status": status,
        "created_at": _now(), "sent_at": None, "fcm_result": None,
    })
    return ref.id


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
# reminders.cancel (Faz Y3, Görev 2) — onaydan sonra çalışan tek kırmızı eylem
# ---------------------------------------------------------------------------


def test_cancel_marks_cancelled_and_stamps_time():
    db = FakeDB()
    reminder_id = _seed(db, "su iç", _future(2))

    out = reminders.cancel(db, reminder_id, now_fn=_now)

    assert "iptal edildi" in out and "su iç" in out
    d = db.collection("reminders").document(reminder_id).get().to_dict()
    assert d["status"] == "cancelled"
    assert d["cancelled_at"] == _now()


def test_cancel_unknown_id_returns_turkish_error_and_writes_nothing():
    db = FakeDB()
    reminder_id = _seed(db, "duruyor", _future(2))

    out = reminders.cancel(db, "yok-boyle-bir-id", now_fn=_now)

    assert out.startswith("Hata")
    # Hiçbir şey yazılmadı: ne yeni doküman, ne de mevcut olanda bir değişiklik.
    docs = list(db.collection("reminders").stream())
    assert len(docs) == 1
    d = db.collection("reminders").document(reminder_id).get().to_dict()
    assert d["status"] == "pending" and "cancelled_at" not in d


def test_cancel_is_idempotent_on_already_cancelled():
    db = FakeDB()
    reminder_id = _seed(db, "su iç", _future(2))
    reminders.cancel(db, reminder_id, now_fn=_now)

    out = reminders.cancel(db, reminder_id, now_fn=lambda: _future(1))

    assert "zaten iptal" in out
    d = db.collection("reminders").document(reminder_id).get().to_dict()
    assert d["status"] == "cancelled"
    assert d["cancelled_at"] == _now()  # ikinci çağrı damgayı EZMEZ


def test_cancel_refuses_a_sent_reminder():
    """Gönderilmiş bir hatırlatma iptal EDİLEMEZ — geçmiş geri alınamaz."""
    db = FakeDB()
    reminder_id = _seed(db, "gitti", _past(1), status="sent")

    out = reminders.cancel(db, reminder_id, now_fn=_now)

    assert "gönderil" in out.lower()
    d = db.collection("reminders").document(reminder_id).get().to_dict()
    assert d["status"] == "sent" and "cancelled_at" not in d


def test_cancel_rejects_blank_id_without_touching_firestore():
    class BoomDB:
        def collection(self, name):
            raise AssertionError("boş id için Firestore'a dokunulmamalı")

    assert reminders.cancel(BoomDB(), "  ", now_fn=_now).startswith("Hata")


def test_cancelled_reminder_leaves_the_pending_list():
    db = FakeDB()
    reminder_id = _seed(db, "gidecek", _future(2))
    _seed(db, "kalacak", _future(3))

    reminders.cancel(db, reminder_id, now_fn=_now)

    assert [r["text"] for r in reminders.list_reminders(db)["hatirlatmalar"]] == ["kalacak"]


def test_cancelled_reminder_is_never_dispatched():
    db = FakeDB()
    reminder_id = _seed(db, "iptal", _past(1))
    reminders.cancel(db, reminder_id, now_fn=_now)
    sent = []

    out = reminders.dispatch_due(db, lambda _db, r: sent.append(r["text"]) or {"ok": True}, now_fn=_now)

    assert out == {"handled": True, "sent": 0, "failed": 0} and sent == []


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


# -- dispatch_due must not undo an approved cancellation (review C2) ----------
# The tick reads its candidates, then spends network time in send_fn. A
# cancellation approved in that window (Y3: cancel_reminder is a RED tool, so
# Kadir signed for it) must survive -- writing the pre-read status back would
# silently reverse a decision the approval centre made.


def test_a_cancellation_landing_mid_dispatch_survives_a_failed_send():
    db = FakeDB()
    set_reminder(db, "su iç", "2026-07-31T15:00:00Z", now_fn=lambda: "2026-07-31T14:00:00Z")
    rid = next(iter(db.collection(REMINDERS_COLLECTION).docs))

    def send_and_cancel_midway(_db, _reminder):
        cancel(db, rid)                       # Kadir's approval lands mid-send
        return {"ok": False, "error": "fcm down"}

    dispatch_due(db, send_and_cancel_midway, now_fn=lambda: "2026-07-31T16:00:00Z")

    assert db.collection(REMINDERS_COLLECTION).docs[rid]["status"] == STATUS_CANCELLED


def test_a_cancellation_landing_mid_dispatch_survives_a_successful_send():
    db = FakeDB()
    set_reminder(db, "su iç", "2026-07-31T15:00:00Z", now_fn=lambda: "2026-07-31T14:00:00Z")
    rid = next(iter(db.collection(REMINDERS_COLLECTION).docs))

    def send_and_cancel_midway(_db, _reminder):
        cancel(db, rid)
        return {"ok": True}

    dispatch_due(db, send_and_cancel_midway, now_fn=lambda: "2026-07-31T16:00:00Z")

    # The push did go out (nothing can unsend it), but the record must not claim
    # the reminder is "sent" -- it was cancelled, and a later reader would
    # otherwise see a cancelled reminder resurrected as delivered.
    assert db.collection(REMINDERS_COLLECTION).docs[rid]["status"] == STATUS_CANCELLED


def test_an_ordinary_failed_send_still_records_the_error_and_stays_pending():
    db = FakeDB()
    set_reminder(db, "su iç", "2026-07-31T15:00:00Z", now_fn=lambda: "2026-07-31T14:00:00Z")
    rid = next(iter(db.collection(REMINDERS_COLLECTION).docs))

    out = dispatch_due(db, lambda *_: {"ok": False, "error": "fcm down"},
                       now_fn=lambda: "2026-07-31T16:00:00Z")

    doc = db.collection(REMINDERS_COLLECTION).docs[rid]
    assert doc["status"] == STATUS_PENDING
    assert doc["fcm_result"] == {"ok": False, "error": "fcm down"}
    assert out["failed"] == 1
