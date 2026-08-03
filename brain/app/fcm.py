"""FCM bildirim gönderimi ve cihaz token kaydı.

Model (Firestore `fcm_tokens` koleksiyonu): her cihaz tek doküman —
{token: str, user_id: str, updated_at: str}. Doküman kimliği token SHA-256 hash'idir.

Model (FCM HTTP v1 API):
POST https://fcm.googleapis.com/v1/projects/{JARVIS_FCM_PROJECT_ID}/messages:send
Authorization: Bearer <access_token> (google.auth.default ile alınır)

Cihaz token'ı yoksa bildirim bir chat oturumuna düşer (Kadir'in varsayılan
e-postasına model mesajı olarak eklenir) — varsayılan oturum "reminders".
Tek istisna `fallback_text=None`: "çağıran sohbete zaten yazdı, düşürme".
Onay kartını sohbete sink'in kendisi yazdığı için `send_approval` bunu kullanır
(aksi hâlde token'sız her onay iki satır olurdu).

Gönderim tek yoldan geçer: `dispatch()`. `send_reminder` (Y2.4) ve
`send_approval` (Y3) onun ince sarmalayıcılarıdır — bildirim türü eklemek bir
sarmalayıcı yazmaktır, HTTP/token/fallback mantığını kopyalamak değil (spec §8).
"""
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import config, messages, tasks

FCM_TOKENS_COLLECTION = "fcm_tokens"
FCM_PROJECT_ID = os.environ.get("JARVIS_FCM_PROJECT_ID", "your-gcp-project")
FCM_ENDPOINT = f"https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send"
REMINDERS_SESSION_ID = "reminders"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def doc_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_token(db, user_id: str, token: str, now_fn=_now) -> dict:
    """Cihaz token'ını `fcm_tokens` koleksiyonuna kaydeder/günceller.
    
    Doküman ID'si token'ın SHA-256 hash'idir (tek cihaz = tek doküman).
    """
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Geçerli bir FCM token'ı gerekli")
    token_str = token.strip()
    key = doc_id(token_str)
    now = now_fn()
    doc = {
        "token": token_str,
        "user_id": user_id,
        "updated_at": now,
    }
    db.collection(FCM_TOKENS_COLLECTION).document(key).set(doc, merge=True)
    logging.info("fcm: token kaydedildi user_id=%s doc_id=%s", user_id, key)
    return {"ok": True}


def _get_access_token() -> str:
    """Google SA access token alır (Cloud Platform scope)."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def _report_to_chat(db, text: str, session_id: str) -> dict:
    """Cihaz token'ı bulunamadığında bildirimi chat oturumuna düşer."""
    owner = tasks.default_owner()
    if not owner:
        logging.warning("fcm: token yok ve default_owner bulunamadı")
        return {"ok": False, "error": "no_owner_and_no_fcm_tokens"}

    store = messages.MessageStore(db)
    store.append(owner, session_id, "model", text)
    logging.info("fcm: token yok, bildirim chat oturumuna dusuruldu (%s)", session_id)
    # Etiket oturumdan TÜRETİLİR; hatırlatma yolu için bu, Y3 öncesiyle aynı
    # "chat_reminders" değeridir (davranış pimi: tests/test_fcm.py).
    return {"ok": True, "fallback": f"chat_{session_id}", "reason": "no_fcm_tokens"}


def dispatch(db, *, title: str, body: str, data: dict, fallback_text: str | None,
             session_id: str = REMINDERS_SESSION_ID, send_http_fn=None) -> dict:
    """Kayıtlı her cihaz token'ına bir FCM HTTP v1 bildirimi gönderir.

    Bildirim türünden bağımsız TEK yol budur (spec §8): hatırlatma da onay da
    buradan geçer, sarmalayıcılar yalnızca metinleri ve `data` alanlarını kurar.

    Cihaz token'ı yoksa bildirim sohbete düşer — kaybolmaz. `session_id` bu
    düşüşün nereye olacağını söyler; varsayılan, Y2.4'ten beri kullanılan
    "reminders" oturumudur.

    `fallback_text=None` bunun İSTİSNASIDIR ve "çağıran sohbete ZATEN yazdı,
    düşürme" demektir: onay kartı spec §7 gereği transcript'e `kind="approval"`
    satırı olarak kendisi düşer, bir de bildirim düşerse Kadir aynı onayı İKİ
    satır olarak görür. O durumda hiçbir şey yazılmaz ve
    `{"ok": False, "reason": "no_fcm_tokens"}` döner — onay yine kaybolmaz,
    çünkü kart sohbette ve kuyruk `GET /api/approvals` ile senkronlanıyor (§4.3).

    `data` değerleri string OLMALIDIR (FCM HTTP v1 sözleşmesi); sarmalayıcılar
    stringify eder. `send_http_fn` testlerde HTTP'yi taklit etmek için enjekte
    edilir — verildiğinde access token da istenmez, yani gerçek ağa ve
    google.auth'a hiç dokunulmaz.
    """
    snaps = list(db.collection(FCM_TOKENS_COLLECTION).stream())
    tokens = [snap.to_dict().get("token") for snap in snaps if snap.to_dict().get("token")]

    if not tokens:
        if fallback_text is None:
            logging.info("fcm: token yok; çağıran sohbete zaten yazdı, düşürülmedi")
            return {"ok": False, "reason": "no_fcm_tokens"}
        return _report_to_chat(db, fallback_text, session_id)

    try:
        access_token = _get_access_token() if send_http_fn is None else "mock_token"
    except Exception as exc:
        logging.exception("fcm: access token alınamadı")
        return {"ok": False, "error": f"access_token_error: {exc}"}

    results = []
    has_success = False

    for token in tokens:
        payload = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": data,
            }
        }

        if send_http_fn:
            res = send_http_fn(FCM_ENDPOINT, payload, access_token)
            results.append(res)
            if res.get("ok"):
                has_success = True
            continue

        # Gerçek HTTP isteği
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                FCM_ENDPOINT,
                data=req_data,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = json.loads(resp.read().decode("utf-8"))
                results.append({"ok": True, "token": token, "response": resp_body})
                has_success = True
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            results.append({"ok": False, "token": token, "status": exc.code, "error": err_body})
        except Exception as exc:
            results.append({"ok": False, "token": token, "error": str(exc)})

    # 404 = kayıt ölü (FCM v1: UNREGISTERED — uygulama silinmiş ya da token
    # dönmüş) ve KALICIDIR: budanmazsa her gönderim sonsuza dek cesede de gider.
    # Yalnız 404 budanır — 5xx/ağ hatası geçicidir ve bir FCM kesintisinde
    # bütün kayıtları silmek push'u kalıcı öldürürdü.
    for r in results:
        if not r.get("ok") and r.get("status") == 404:
            key = doc_id(r["token"])
            db.collection(FCM_TOKENS_COLLECTION).document(key).delete()
            logging.info("fcm: ölü token budandı doc_id=%s", key[:12])

    # Akıbet BURADA loglanır, çünkü başka hiçbir yerde loglanmıyor: sink dönüş
    # değerini bilinçli yok sayar (best-effort sözleşmesi) ve 4 Ağu 01:16
    # vakasında push'un gidip gitmediği bu yüzden teşhis edilemedi. Token TAM
    # değeriyle yazılmaz — cihaza gönderim yetkisidir; önek teşhise yeter.
    basarili = sum(1 for r in results if r.get("ok"))
    ozet = ", ".join(
        f"{str(r.get('token'))[:8]}…:"
        + ("ok" if r.get("ok") else str(r.get("status") or r.get("error") or "?")[:120])
        for r in results)
    if has_success:
        logging.info("fcm: dispatch %d/%d token'a ulaştı [%s]", basarili, len(results), ozet)
        return {"ok": True, "fcm_results": results}
    logging.warning("fcm: dispatch %d/%d — HİÇBİR token'a ulaşılamadı [%s]",
                    basarili, len(results), ozet)
    return {"ok": False, "error": "fcm_dispatch_failed", "fcm_results": results}


def send_reminder(db, reminder: dict, send_http_fn=None) -> dict:
    """Hatırlatmayı kaydedilmiş cihaz token'larına FCM HTTP v1 ile gönderir.

    Cihaz token'ı yoksa fallback olarak chat oturumuna düşer.
    `send_http_fn` testlerde HTTP çağrısını taklit etmek (mock) için enjekte edilebilir.

    Y3'ten beri `dispatch`'in ince bir sarmalayıcısıdır; payload'ı, fallback
    metni ve dönüş şekli Y3 ÖNCESİYLE birebir aynıdır.
    """
    return dispatch(
        db,
        title="Hatırlatma",
        body=reminder.get("text", ""),
        data={
            "reminder_id": str(reminder.get("id", "")),
            "due_at": str(reminder.get("due_at", "")),
        },
        fallback_text=f"⏰ Hatırlatma: {reminder.get('text', '')}",
        session_id=REMINDERS_SESSION_ID,
        send_http_fn=send_http_fn,
    )


def send_approval(db, approval: dict, send_http_fn=None) -> dict:
    """Bekleyen bir onayı push olarak gönderir (Faz Y3, spec §8).

    `data.approval_id` sözleşmedir: Android bildirime dokunduğunda uygulamayı
    açıp o kartı gösterir (spec §10). Push kaçarsa onay KAYBOLMAZ — kart zaten
    transcript'te (spec §7) ve kuyruk `GET /api/approvals` ile senkronlanır
    (§4.3).

    `fallback_text` bilinçli olarak None: bu onayın kartını sohbete ÇAĞIRAN
    (main._approval_sink) zaten yazdı. Sohbet fallback'i burada da açık olsaydı
    cihaz token'ı olmayan her onay Kadir'e İKİ satır olarak görünürdü.
    `send_reminder`'ın fallback'i bundan etkilenmez: kimse onun adına sohbete
    yazmıyor.
    """
    return dispatch(
        db,
        title="Onay bekliyor",
        body=str(approval.get("title", "")),
        data={"approval_id": str(approval.get("id", ""))},
        fallback_text=None,
        send_http_fn=send_http_fn,
    )
