"""Tests for app/fcm.py (Faz Y3, Görev 3): genel `dispatch` + iki sarmalayıcı.

GERÇEK AĞ YOK: HTTP çağrısı her testte `send_http_fn` ile enjekte edilir
(mevcut desen). `send_http_fn` verildiğinde fcm.py access token da istemez, yani
google.auth'a da dokunulmaz.

Taşıyıcı pim: `test_send_reminder_payload_is_byte_identical_to_pre_y3` —
`send_reminder` `dispatch`'in ince bir sarmalayıcısı hâline geldi, DAVRANIŞI
değişmedi (spec §8).
"""
from unittest.mock import patch

from app import fcm, messages
from tests.fakes import FakeDB

OWNER = "kadir@example.com"


class Recorder:
    """Enjekte edilen HTTP: çağrıları kaydeder, sabit bir sonuç döner."""

    def __init__(self, ok=True):
        self.calls = []
        self._ok = ok

    def __call__(self, url, payload, token):
        self.calls.append({"url": url, "payload": payload, "token": token})
        return {"ok": self._ok, "token": payload["message"]["token"]}

    @property
    def payloads(self):
        return [c["payload"]["message"] for c in self.calls]


def _chat_rows(db):
    return [s.to_dict() for s in db.collection(messages.COLLECTION).stream()]


# ---------------------------------------------------------------------------
# dispatch() — token'lı yol
# ---------------------------------------------------------------------------


def test_dispatch_sends_title_body_and_data_to_every_token():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")
    fcm.register_token(db, "u", "tok-2")
    http = Recorder()

    out = fcm.dispatch(db, title="Başlık", body="Gövde", data={"k": "v"},
                       fallback_text="düşerse bu", send_http_fn=http)

    assert out["ok"] is True and len(out["fcm_results"]) == 2
    assert {m["token"] for m in http.payloads} == {"tok-1", "tok-2"}
    for m in http.payloads:
        assert m["notification"] == {"title": "Başlık", "body": "Gövde"}
        assert m["data"] == {"k": "v"}
    assert all(c["url"] == fcm.FCM_ENDPOINT for c in http.calls)
    # Token varken sohbete HİÇBİR şey düşmez.
    assert _chat_rows(db) == []


def test_dispatch_reports_failure_when_every_token_fails():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")

    out = fcm.dispatch(db, title="T", body="B", data={}, fallback_text="f",
                       send_http_fn=Recorder(ok=False))

    assert out["ok"] is False and out["error"] == "fcm_dispatch_failed"
    assert len(out["fcm_results"]) == 1


def test_dispatch_succeeds_when_at_least_one_token_works():
    db = FakeDB()
    fcm.register_token(db, "u", "iyi")
    fcm.register_token(db, "u", "kotu")

    def half(url, payload, token):
        return {"ok": payload["message"]["token"] == "iyi"}

    assert fcm.dispatch(db, title="T", body="B", data={}, fallback_text="f",
                        send_http_fn=half)["ok"] is True


# ---------------------------------------------------------------------------
# dispatch() — token'sız yol (sohbete düşme)
# ---------------------------------------------------------------------------


def test_dispatch_without_tokens_falls_back_to_chat():
    db = FakeDB()
    http = Recorder()

    with patch("app.tasks.default_owner", return_value=OWNER):
        out = fcm.dispatch(db, title="T", body="B", data={"k": "v"},
                           fallback_text="⏰ sohbete düşen metin", send_http_fn=http)

    assert out == {"ok": True, "fallback": "chat_reminders", "reason": "no_fcm_tokens"}
    assert http.calls == []  # token yoksa HTTP hiç denenmez
    rows = _chat_rows(db)
    assert len(rows) == 1
    assert rows[0]["text"] == "⏰ sohbete düşen metin"
    assert rows[0]["role"] == "model"
    assert rows[0]["user_id"] == OWNER
    assert rows[0]["session_id"] == fcm.REMINDERS_SESSION_ID


def test_dispatch_falls_back_into_the_requested_session():
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=OWNER):
        out = fcm.dispatch(db, title="T", body="B", data={}, fallback_text="metin",
                           session_id="web-2026-08-03")

    assert out["fallback"] == "chat_web-2026-08-03"
    assert _chat_rows(db)[0]["session_id"] == "web-2026-08-03"


def test_dispatch_without_tokens_and_without_owner_reports_failure():
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=None):
        out = fcm.dispatch(db, title="T", body="B", data={}, fallback_text="metin")

    assert out == {"ok": False, "error": "no_owner_and_no_fcm_tokens"}
    assert _chat_rows(db) == []


# ---------------------------------------------------------------------------
# send_reminder — davranış pimi (Y3 öncesiyle BİREBİR aynı)
# ---------------------------------------------------------------------------


def test_send_reminder_payload_is_byte_identical_to_pre_y3():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")
    http = Recorder()

    out = fcm.send_reminder(db, {"id": "r1", "text": "su iç", "due_at": "2026-08-03T15:00:00+00:00"},
                            send_http_fn=http)

    assert out["ok"] is True and "fcm_results" in out
    assert http.payloads == [{
        "token": "tok-1",
        "notification": {"title": "Hatırlatma", "body": "su iç"},
        "data": {"reminder_id": "r1", "due_at": "2026-08-03T15:00:00+00:00"},
    }]


def test_send_reminder_fallback_is_byte_identical_to_pre_y3():
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=OWNER):
        out = fcm.send_reminder(db, {"id": "r1", "text": "su iç"})

    assert out == {"ok": True, "fallback": "chat_reminders", "reason": "no_fcm_tokens"}
    rows = _chat_rows(db)
    assert rows[0]["text"] == "⏰ Hatırlatma: su iç"
    assert rows[0]["session_id"] == fcm.REMINDERS_SESSION_ID


def test_send_reminder_stringifies_missing_fields_like_before():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")
    http = Recorder()

    fcm.send_reminder(db, {}, send_http_fn=http)

    assert http.payloads[0]["data"] == {"reminder_id": "", "due_at": ""}
    assert http.payloads[0]["notification"]["body"] == ""


# ---------------------------------------------------------------------------
# send_approval — onay kartının push karşılığı
# ---------------------------------------------------------------------------


def _approval(**over):
    doc = {"id": "a1", "kind": "tool_call", "title": "'su iç' hatırlatması silinecek",
           "detail": "kırmızı bölge", "session_id": "web-2026-08-03"}
    doc.update(over)
    return doc


def test_send_approval_carries_approval_id_in_data():
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")
    http = Recorder()

    out = fcm.send_approval(db, _approval(), send_http_fn=http)

    assert out["ok"] is True
    message = http.payloads[0]
    assert message["data"]["approval_id"] == "a1"
    assert message["notification"]["body"] == "'su iç' hatırlatması silinecek"
    # FCM data alanlarının hepsi string olmak ZORUNDA (HTTP v1 sözleşmesi).
    assert all(isinstance(v, str) for v in message["data"].values())


def test_send_approval_without_tokens_falls_back_to_the_approvals_own_session():
    """Kart §7 gereği zaten sohbette; push kaçarsa bildirim de AYNI sohbete
    düşer, 'reminders' oturumuna değil."""
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=OWNER):
        out = fcm.send_approval(db, _approval())

    assert out["ok"] is True and out["reason"] == "no_fcm_tokens"
    assert _chat_rows(db)[0]["session_id"] == "web-2026-08-03"


def test_send_approval_without_session_falls_back_to_the_default_session():
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=OWNER):
        fcm.send_approval(db, _approval(session_id=None))

    assert _chat_rows(db)[0]["session_id"] == fcm.REMINDERS_SESSION_ID
