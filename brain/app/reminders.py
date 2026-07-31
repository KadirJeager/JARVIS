"""Hatırlatma mantığı (North Star §4.4, Faz Y2.4): zamanlanmış hatırlatmalar ve bildirim dağıtımı.

Model (Firestore `reminders` koleksiyonu): her hatırlatma tek doküman —
{text: str, due_at: str (ISO UTC), status: "pending"|"sent"|"cancelled",
 created_at: str, sent_at: str|None, fcm_result: dict|str|None}.
"""
import logging
from datetime import datetime, timezone

from google.cloud.firestore_v1.base_query import FieldFilter

REMINDERS_COLLECTION = "reminders"
STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str: str) -> datetime | None:
    """ISO-8601 string'ini UTC datetime nesnesine dönüştürür."""
    if not isinstance(iso_str, str):
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def set_reminder(db, text: str, due_at: str, now_fn=_now) -> str:
    """Belirli bir zaman için hatırlatma kurar.
    
    due_at ISO-8601 biçiminde olmalıdır (ör. '2026-07-31T15:00:00Z').
    Geçmiş tarih verildiğinde exception fırlatmaz, Türkçe hata metni döner.
    """
    if not text or not text.strip():
        return "Hata: Hatırlatma metni boş olamaz."
    
    parsed_due = _parse_iso(due_at)
    if parsed_due is None:
        return "Hata: Geçersiz tarih biçimi. ISO-8601 biçiminde (ör. 2026-07-31T15:00:00Z) olmalı."
    
    now_str = now_fn()
    parsed_now = _parse_iso(now_str)
    
    if parsed_due <= parsed_now:
        return "Hata: Hatırlatma zamanı geçmiş bir tarih olamaz."

    due_at_utc = parsed_due.isoformat()
    doc = {
        "text": text.strip(),
        "due_at": due_at_utc,
        "status": STATUS_PENDING,
        "created_at": now_str,
        "sent_at": None,
        "fcm_result": None,
    }
    
    _, ref = db.collection(REMINDERS_COLLECTION).add(doc)
    logging.info("reminders: set_reminder id=%s text=%r due_at=%s", ref.id, text, due_at_utc)
    return f"Hatırlatma kuruldu: '{text.strip()}' ({due_at_utc}) [ID: {ref.id}]"


def list_reminders(db) -> dict:
    """Bekleyen hatırlatmaları zaman sırasına göre listeler."""
    snaps = list(
        db.collection(REMINDERS_COLLECTION)
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    
    items = []
    for snap in snaps:
        d = snap.to_dict()
        items.append({
            "id": snap.reference.id,
            "text": d.get("text"),
            "due_at": d.get("due_at"),
            "created_at": d.get("created_at"),
        })
    
    items.sort(key=lambda x: x.get("due_at") or "")
    return {"hatirlatmalar": items, "sayi": len(items)}


def dispatch_due(db, send_fn, now_fn=_now) -> dict:
    """Zamanı gelen (due_at <= now) ve status=pending olan hatırlatmaları dağıtır.
    
    send_fn(db, reminder) çağrılır:
    - Başarılı ise status='sent', sent_at=now, fcm_result=sonuç.
    - Hata verirse status='pending' kalır, fcm_result=hata detayı.
    """
    now_str = now_fn()
    parsed_now = _parse_iso(now_str)
    
    snaps = list(
        db.collection(REMINDERS_COLLECTION)
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    
    sent_count = 0
    failed_count = 0
    
    for snap in snaps:
        d = snap.to_dict()
        due_str = d.get("due_at", "")
        parsed_due = _parse_iso(due_str)
        
        if parsed_due is None or parsed_due > parsed_now:
            continue
        
        reminder = dict(d)
        reminder["id"] = snap.reference.id
        
        try:
            res = send_fn(db, reminder)
            success = isinstance(res, dict) and res.get("ok") is True
        except Exception as exc:
            logging.exception("reminders: dispatch_due send_fn hatası id=%s", reminder["id"])
            res = {"ok": False, "error": str(exc)}
            success = False

        ref = snap.reference
        if success:
            update = {
                "status": STATUS_SENT,
                "sent_at": now_str,
                "fcm_result": res,
            }
            sent_count += 1
            logging.info("reminders: dispatch_due gonderildi id=%s", reminder["id"])
        else:
            update = {
                "status": STATUS_PENDING,
                "fcm_result": res,
            }
            failed_count += 1
            logging.warning("reminders: dispatch_due basarisiz id=%s res=%s", reminder["id"], res)
            
        ref.set(update, merge=True)

    return {"handled": True, "sent": sent_count, "failed": failed_count}
