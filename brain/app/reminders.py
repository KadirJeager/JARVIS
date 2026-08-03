"""Hatırlatma mantığı (North Star §4.4, Faz Y2.4): zamanlanmış hatırlatmalar ve bildirim dağıtımı.

Model (Firestore `reminders` koleksiyonu): her hatırlatma tek doküman —
{text: str, due_at: str (ISO UTC), status: "pending"|"sent"|"cancelled",
 created_at: str, sent_at: str|None, cancelled_at: str|None,
 fcm_result: dict|str|None}.

`cancel()` (Faz Y3) bu modülün tek YIKICI işlemidir: kaydı silmez, `cancelled`
damgalar — geçmiş okunabilir kalsın diye. Kırmızı bölgededir ve normal akışta
yalnızca Kadir'in onayından sonra, `approvals.decide()` üzerinden çağrılır
(app/tools.py'deki yürütücü kaydı).
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


def cancel(db, reminder_id: str, now_fn=_now) -> str:
    """Bekleyen bir hatırlatmayı iptal eder; Türkçe sonuç metni döner.

    Modül deseni (set_reminder ile aynı): exception FIRLATMAZ, gözlem döner —
    bu metin hem modele hem de onay kaydının `outcome` alanına gider.

    Kayıt silinmez, `status=cancelled` + `cancelled_at` damgalanır: iptal de bir
    olaydır, geçmişten kazınmaz. Üç durum ayrı ayrı ele alınır:
    - yoksa: hata metni, HİÇBİR yazma yapılmaz (var olmayan id'ye doküman
      yaratmak — Firestore'da set() bunu sessizce yapar — iptali bir yazma
      aracına çevirirdi);
    - zaten `cancelled`: idempotent, ilk damga korunur (çift dokunuş, ör. push
      + kuyruk senkronu, damgayı ileri kaydırmasın);
    - `sent`: iptal EDİLEMEZ — bildirim gitmiştir, geçmiş geri alınamaz.
    """
    if not isinstance(reminder_id, str) or not reminder_id.strip():
        return "Hata: Hatırlatma ID'si gerekli."

    ref = db.collection(REMINDERS_COLLECTION).document(reminder_id.strip())
    snap = ref.get()
    if not snap.exists:
        return f"Hata: '{reminder_id.strip()}' kimlikli bir hatırlatma bulunamadı."

    d = snap.to_dict()
    text = d.get("text", "")
    status = d.get("status")

    if status == STATUS_CANCELLED:
        return f"'{text}' hatırlatması zaten iptal edilmişti."
    if status == STATUS_SENT:
        return f"'{text}' hatırlatması zaten gönderilmiş; gönderilmiş bir hatırlatma iptal edilemez."

    ref.set({"status": STATUS_CANCELLED, "cancelled_at": now_fn()}, merge=True)
    logging.info("reminders: cancel id=%s text=%r", reminder_id.strip(), text)
    return f"Hatırlatma iptal edildi: '{text}'"


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
        # RE-READ before writing. The candidate list is a snapshot taken before
        # send_fn, and send_fn is network time (an FCM round trip per token). A
        # cancellation can land inside that window -- and since Faz Y3 that
        # cancellation is a RED action Kadir personally approved
        # (tools.cancel_reminder -> approvals.decide). Writing the pre-read status
        # back would silently reverse his decision: the failure branch used to
        # restore "pending" unconditionally, resurrecting a cancelled reminder for
        # the next tick, and the success branch would stamp it "sent".
        #
        # Not atomic -- a compare-and-set would need a transaction. It closes the
        # window that actually exists (hundreds of ms of network I/O) and leaves
        # only the microseconds between this read and the write. The failure
        # direction is also the safe one: worst case a cancelled reminder keeps its
        # cancelled status and one push already went out, which nothing can unsend.
        current = ref.get()
        if not current.exists or current.to_dict().get("status") != STATUS_PENDING:
            logging.info(
                "reminders: dispatch_due id=%s artik pending degil (%s) -- durum "
                "YAZILMADI (iptal/karar korunuyor)",
                reminder["id"],
                current.to_dict().get("status") if current.exists else "silinmis",
            )
            if success:
                sent_count += 1
            else:
                failed_count += 1
            continue

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
