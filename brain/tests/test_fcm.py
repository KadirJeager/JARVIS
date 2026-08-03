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
# dispatch() — sonuç LOGLANIR (4 Ağu 01:16 vakası: onay push'u hiç ulaşmadı ve
# dispatch sonucu loglamadığı için akıbeti loglar açıkken bile görünmezdi —
# sink dönüş değerini bilinçli yok sayar, tek iz bu satırdır)
# ---------------------------------------------------------------------------


def test_dispatch_logs_the_outcome_without_leaking_tokens(caplog):
    """ÖLDÜREN MUTASYON: dispatch sonundaki özet logunu silmek — push yine
    sessizce ölür. Token TAM değeriyle loglanamaz: FCM token'ı cihaza gönderim
    yetkisidir, log bir sızıntı kanalı olamaz (önek yeter, teşhis için)."""
    import logging as _logging

    db = FakeDB()
    fcm.register_token(db, "u", "tok-cok-gizli-uzun-deger-1234567890")

    with caplog.at_level(_logging.INFO):
        fcm.dispatch(db, title="T", body="B", data={}, fallback_text="f",
                     send_http_fn=Recorder())

    assert "fcm: dispatch 1/1" in caplog.text
    assert "tok-cok-gizli-uzun-deger-1234567890" not in caplog.text


def test_dispatch_prunes_a_token_fcm_reports_unregistered():
    """404/UNREGISTERED = kayıt ölü (FCM v1 sözleşmesi: uygulama silinmiş ya da
    token dönmüş). Budanmazsa her gönderim sonsuza dek cesede de gider — 4 Ağu
    gecesi 15 saatlik ölü bir token her dispatch'te 404 üretiyordu.

    ÖLDÜREN MUTASYON: budama dalını silmek — ölü token koleksiyonda kalır."""
    db = FakeDB()
    fcm.register_token(db, "u", "olu-token")
    fcm.register_token(db, "u", "iyi-token")

    def http(url, payload, token):
        t = payload["message"]["token"]
        if t == "olu-token":
            return {"ok": False, "token": t, "status": 404, "error": "UNREGISTERED"}
        return {"ok": True, "token": t}

    out = fcm.dispatch(db, title="T", body="B", data={}, fallback_text="f",
                       send_http_fn=http)

    kalan = [s.to_dict()["token"]
             for s in db.collection(fcm.FCM_TOKENS_COLLECTION).stream()]
    assert kalan == ["iyi-token"]
    assert out["ok"] is True


def test_a_transient_failure_does_not_prune_the_token():
    """5xx/ağ hatası GEÇİCİDİR: budamak, bir FCM kesintisinde bütün cihaz
    kayıtlarını silip push'u kalıcı olarak öldürürdü. Yalnız 404 budanır."""
    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")

    def http(url, payload, token):
        return {"ok": False, "token": payload["message"]["token"],
                "status": 500, "error": "backend hiccup"}

    fcm.dispatch(db, title="T", body="B", data={}, fallback_text="f",
                 send_http_fn=http)

    kalan = [s.to_dict()["token"]
             for s in db.collection(fcm.FCM_TOKENS_COLLECTION).stream()]
    assert kalan == ["tok-1"]


def test_dispatch_logs_a_warning_when_every_token_fails(caplog):
    """Topyekûn başarısızlık WARNING'dir: INFO akışında kaybolmamalı — bu satır,
    'push gitti mi' sorusunun loglardan cevaplanabildiği TEK yerdir."""
    import logging as _logging

    db = FakeDB()
    fcm.register_token(db, "u", "tok-1")

    with caplog.at_level(_logging.INFO):
        fcm.dispatch(db, title="T", body="B", data={}, fallback_text="f",
                     send_http_fn=Recorder(ok=False))

    kayit = [r for r in caplog.records if r.levelno == _logging.WARNING]
    assert any("0/1" in r.getMessage() for r in kayit)


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


def test_dispatch_with_fallback_text_none_writes_nothing_to_chat():
    """`fallback_text=None` = "çağıran zaten sohbete yazdı, düşürme" (spec §7):
    onay sink'i kartı kendisi yazdığı için ikinci bir satır Kadir'e aynı onayı
    İKİ kez gösterirdi."""
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=OWNER):
        out = fcm.dispatch(db, title="T", body="B", data={}, fallback_text=None)

    assert out == {"ok": False, "reason": "no_fcm_tokens"}
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


def test_send_approval_without_tokens_does_not_duplicate_the_card_in_chat():
    """TAŞIYICI PİM (çift satır tuzağı): kart §7 gereği ZATEN sohbette — onayı
    kuran sink onu `kind="approval"` satırı olarak yazdı. Push kaçtığında
    bildirimin de aynı oturuma düşmesi Kadir'e aynı onayı İKİ satır gösterirdi,
    o yüzden `send_approval` fallback'i bilinçli olarak KAPATIR. Onay
    kaybolmaz: kart sohbette, kuyruk GET /api/approvals ile senkron (§4.3)."""
    db = FakeDB()

    with patch("app.tasks.default_owner", return_value=OWNER):
        out = fcm.send_approval(db, _approval())

    assert out == {"ok": False, "reason": "no_fcm_tokens"}
    assert _chat_rows(db) == []
