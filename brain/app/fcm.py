"""FCM bildirim gönderimi ve cihaz token kaydı.

Model (Firestore `fcm_tokens` koleksiyonu): her cihaz tek doküman —
{token: str, user_id: str, updated_at: str}. Doküman kimliği token SHA-256 hash'idir.

Model (FCM HTTP v1 API):
POST https://fcm.googleapis.com/v1/projects/{JARVIS_FCM_PROJECT_ID}/messages:send
Authorization: Bearer <access_token> (google.auth.default ile alınır)

Cihaz token'ı yoksa bildirim `report_session_id="reminders"` chat oturumuna düşer
(Kadir'in varsayılan e-postasına model mesajı olarak eklenir).
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


def _report_to_chat(db, reminder: dict) -> dict:
    """Cihaz token'ı bulunamadığında bildirimi chat oturumuna düşer."""
    owner = tasks.default_owner()
    if not owner:
        logging.warning("fcm: token yok ve default_owner bulunamadı")
        return {"ok": False, "error": "no_owner_and_no_fcm_tokens"}
    
    store = messages.MessageStore(db)
    text = f"⏰ Hatırlatma: {reminder.get('text', '')}"
    store.append(owner, REMINDERS_SESSION_ID, "model", text)
    logging.info("fcm: token yok, hatirlatma chat oturumuna dusuruldu (%s)", REMINDERS_SESSION_ID)
    return {"ok": True, "fallback": "chat_reminders", "reason": "no_fcm_tokens"}


def send_reminder(db, reminder: dict, send_http_fn=None) -> dict:
    """Hatırlatmayı kaydedilmiş cihaz token'larına FCM HTTP v1 ile gönderir.
    
    Cihaz token'ı yoksa fallback olarak chat oturumuna düşer.
    `send_http_fn` testlerde HTTP çağrısını taklit etmek (mock) için enjekte edilebilir.
    """
    snaps = list(db.collection(FCM_TOKENS_COLLECTION).stream())
    tokens = [snap.to_dict().get("token") for snap in snaps if snap.to_dict().get("token")]
    
    if not tokens:
        return _report_to_chat(db, reminder)

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
                "notification": {
                    "title": "Hatırlatma",
                    "body": reminder.get("text", ""),
                },
                "data": {
                    "reminder_id": str(reminder.get("id", "")),
                    "due_at": str(reminder.get("due_at", "")),
                },
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

    if has_success:
        return {"ok": True, "fcm_results": results}
    return {"ok": False, "error": "fcm_dispatch_failed", "fcm_results": results}
