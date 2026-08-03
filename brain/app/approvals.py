"""Onay merkezi (North Star §4.8/§9, Faz Y3): kırmızı bölge eylemleri Kadir'in
kararını bekleyen bir kuyruğa düşer, karar anında çalışır.

Bugüne kadar kırmızı bölge bir çıkmaz sokaktı: policy callback aracı reddediyor,
model Kadir'e soruyor, Kadir "evet" diyor ve hiçbir şey olmuyordu — "onay"
kavramının sistemde bir temsili yoktu. Bu modül o temsili kurar.

Model (Firestore):
- `approvals` (auto-id): {user_id, kind, title, detail, tool_name, tool_args,
  zone, session_id, status, created_at, expires_at, decided_at, decided_by,
  outcome}. status: pending|approved|rejected|expired|failed.
- `approval_claims` (doc id = onay id): {decision, by, at}. Kararın BİRİNCİL
  kaydı budur; onay dokümanındaki `status` onun izdüşümüdür.

Üç değişmez bu modülün varlık sebebidir:

1. **Zaman aşımı = reddet ve KARAR anında uygulanır** (spec §4.1). Süpürücü iş
   (`expire_due`) tek başına yeterli değildir: süpürme ile son kullanma arasındaki
   pencerede gelen bir onay, süresi geçmiş kırmızı bir eylemi çalıştırırdı.
   `decide()` bu yüzden yürütücüden ÖNCE süreyi kontrol eder. Süpürücü bir
   temizlik/bildirim yoludur, güvenlik sınırı değil.
2. **Karar idempotenttir** (spec §4.2). Firestore'da işlemsiz koşullu güncelleme
   yoktur, ama `DocumentReference.create()` atomik bir "yoksa yaz"dır ve
   `AlreadyExists` fırlatır (aynı desen: conversations.py başlık yarışı). Çift
   dokunuş — push bildirimi + kuyruk senkronu aynı anda — kırmızı bir eylemi iki
   kez çalıştıramaz.
3. **Yürütme bir allowlist'tir** (spec §6). `executors` sözlüğünde kayıtlı
   olmayan bir `tool_name` ÇALIŞMAZ; onay kaydı, keyfi bir isim yazarak rastgele
   kod çalıştırmanın yolu değildir.

Zaman karşılaştırmaları (süre doldu mu) bilinçli olarak Python tarafındadır,
Firestore sorgusunda değil: `status == pending` eşitlik filtresi + `expires_at`
aralık filtresi bir composite index ister; kuyruk zaten MAX_PENDING ile sınırlı
olduğu için karşılaştırmayı burada yapmak hem indekssiz hem de daha okunur.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from . import config

COLLECTION = "approvals"
CLAIMS_COLLECTION = "approval_claims"

KIND_TOOL_CALL = "tool_call"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"
# Sadece decide()'ın DÖNÜŞ değerinde geçer, dokümanda asla: sahibi olmayan ve hiç
# var olmayan onay BİREBİR aynı cevabı alır, böylece uç 404 verirken başkasının
# onayının varlığı bile sızmaz (spec §4.4 — 403 değil, 404).
STATUS_NOT_FOUND = "not_found"

DECISIONS = (STATUS_APPROVED, STATUS_REJECTED)
MAX_PENDING = 50

EXPIRED_OUTCOME = "Onay süresi doldu; eylem çalıştırılmadı."
NO_EXECUTOR_OUTCOME = "bu araç onaydan sonra çalıştırılamıyor (yürütücü kayıtlı değil)"

# (tool_args, user_id) -> Türkçe sonuç metni
Executor = Callable[[dict, str], str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str) -> datetime | None:
    """ISO-8601 string'ini UTC datetime'a çevirir; okunamazsa None."""
    if not isinstance(iso_str, str):
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_expired(doc: dict, parsed_now: datetime) -> bool:
    """Süresi doldu mu. Okunamayan/eksik `expires_at` DOLMUŞ sayılır: fail-closed
    — bozuk bir damga kırmızı bir eylemi süresiz onaylanabilir bırakmasın."""
    parsed_expires = _parse_iso(doc.get("expires_at"))
    if parsed_expires is None:
        return True
    return parsed_expires <= parsed_now


def _project(approval_id: str, d: dict) -> dict:
    """Onay dokümanının okuyucuya (uç, kart, test) verilen biçimi."""
    return {
        "id": approval_id,
        "user_id": d.get("user_id"),
        "kind": d.get("kind"),
        "title": d.get("title"),
        "detail": d.get("detail"),
        "tool_name": d.get("tool_name"),
        "tool_args": d.get("tool_args") or {},
        "zone": d.get("zone"),
        "session_id": d.get("session_id"),
        "status": d.get("status"),
        "created_at": d.get("created_at"),
        "expires_at": d.get("expires_at"),
        "decided_at": d.get("decided_at"),
        "decided_by": d.get("decided_by"),
        "outcome": d.get("outcome"),
    }


def _result(status: str, outcome: str | None, already: bool) -> dict:
    return {"status": status, "outcome": outcome, "already": already}


def request(db, *, user_id: str, kind: str, title: str, detail: str,
            tool_name: str | None = None, tool_args: dict | None = None,
            zone: str, session_id: str, now_fn=_now,
            ttl_minutes: int | None = None) -> str:
    """Bekleyen bir onay kaydı kurar, onay id'sini döner.

    `tool_args` değerleri stringify edilip 500 karakterde kesilir —
    policy.write_audit ile AYNI kural: kayıt yeniden kurmak içindir, tam yük
    dökümü için değil. `ttl_minutes` verilmezse config.APPROVAL_TTL_MINUTES.
    """
    if not user_id or not isinstance(user_id, str):
        raise ValueError("onay için user_id gerekli")
    if not title or not isinstance(title, str):
        raise ValueError("onay için title gerekli")
    if kind == KIND_TOOL_CALL and not tool_name:
        raise ValueError("kind=tool_call için tool_name zorunlu")

    now = now_fn()
    parsed_now = _parse_iso(now)
    if parsed_now is None:
        raise ValueError(f"geçersiz zaman damgası: {now!r}")
    if ttl_minutes is None:
        ttl_minutes = config.APPROVAL_TTL_MINUTES

    doc = {
        "user_id": user_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "tool_name": tool_name,
        "tool_args": {k: str(v)[:500] for k, v in (tool_args or {}).items()},
        "zone": zone,
        "session_id": session_id,
        "status": STATUS_PENDING,
        "created_at": now,
        "expires_at": (parsed_now + timedelta(minutes=ttl_minutes)).isoformat(),
        "decided_at": None,
        "decided_by": None,
        "outcome": None,
    }
    _, ref = db.collection(COLLECTION).add(doc)
    logging.info("approvals: request id=%s user=%s kind=%s tool=%s ttl=%s dk",
                 ref.id, user_id, kind, tool_name, ttl_minutes)
    return ref.id


def list_pending(db, user_id: str, now_fn=_now) -> list[dict]:
    """Bu kullanıcının bekleyen onayları, yeniden eskiye, en fazla MAX_PENDING.

    Süresi geçmişler süpürücü henüz koşmamış olsa bile DÖNMEZ (§4.1: zaman aşımı
    süpürücüye bağlı değildir). Sıralama ve süre elemesi Python tarafındadır —
    modül docstring'indeki composite-index gerekçesi."""
    parsed_now = _parse_iso(now_fn())
    snaps = (
        db.collection(COLLECTION)
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    items = [
        _project(snap.reference.id, snap.to_dict())
        for snap in snaps
        if not _is_expired(snap.to_dict(), parsed_now)
    ]
    items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    return items[:MAX_PENDING]


def get(db, approval_id: str, user_id: str) -> dict | None:
    """Tek onayın güncel hali. Başkasının onayı YOK sayılır (spec §4.4):
    çağıran 404 verir, 403 değil — varlığı bile sızmasın."""
    snap = db.collection(COLLECTION).document(approval_id).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    if d.get("user_id") != user_id:
        logging.warning("approvals: get sahiplik reddi id=%s isteyen=%s", approval_id, user_id)
        return None
    return _project(approval_id, d)


def decide(db, approval_id: str, user_id: str, decision: str,
           executors: dict[str, Executor], now_fn=_now) -> dict:
    """Bir onayı karara bağlar ve onaysa eylemi ÇALIŞTIRIR.

    Dönen dict: {status, outcome, already}. `already=True`, bu çağrının kararı
    vermediği (başkası verdi / süre verdi) anlamına gelir; yürütücü çağrılmaz.

    Sıra sözleşmedir, gevşetilemez:
    oku → sahiplik → status pending mi → SÜRE → claim create() → status yaz →
    onaysa yürüt. Süre kontrolü claim'den önce gelir ki süresi geçmiş bir onay
    claim bile üretmesin; yürütme en sonda gelir ki karar kaydı her hâlükârda
    Firestore'a işlenmiş olsun.
    """
    if decision not in DECISIONS:
        raise ValueError(f"geçersiz karar: {decision!r} (beklenen: {DECISIONS})")

    ref = db.collection(COLLECTION).document(approval_id)
    snap = ref.get()
    if not snap.exists:
        return _result(STATUS_NOT_FOUND, None, False)
    d = snap.to_dict()
    if d.get("user_id") != user_id:
        logging.warning("approvals: decide sahiplik reddi id=%s isteyen=%s",
                        approval_id, user_id)
        return _result(STATUS_NOT_FOUND, None, False)

    if d.get("status") != STATUS_PENDING:
        return _result(d.get("status"), d.get("outcome"), True)

    now = now_fn()
    parsed_now = _parse_iso(now)
    if _is_expired(d, parsed_now):
        # §4.1: zaman aşımı KARAR anında da uygulanır. decided_by yazılmaz —
        # kararı kimse vermedi, süre verdi.
        ref.set({"status": STATUS_EXPIRED, "decided_at": now,
                 "outcome": EXPIRED_OUTCOME}, merge=True)
        logging.info("approvals: decide süresi dolmuş id=%s (yürütme yok)", approval_id)
        return _result(STATUS_EXPIRED, EXPIRED_OUTCOME, True)

    try:
        db.collection(CLAIMS_COLLECTION).document(approval_id).create(
            {"decision": decision, "by": user_id, "at": now})
    except AlreadyExists:
        # Yarışı kaybettik: kararı başka bir dokunuş verdi. Tek gerçek kaynak
        # onay dokümanıdır — yeniden okunur (kazanan status'ü henüz yazmamış
        # olabilir; o zaman hâlâ pending görünür, karar yine de bizim değildir).
        current = ref.get().to_dict() or {}
        logging.info("approvals: decide claim yarışı kaybedildi id=%s", approval_id)
        return _result(current.get("status"), current.get("outcome"), True)

    ref.set({"status": decision, "decided_at": now, "decided_by": user_id}, merge=True)

    if decision == STATUS_REJECTED:
        logging.info("approvals: reddedildi id=%s by=%s", approval_id, user_id)
        return _result(STATUS_REJECTED, None, False)

    if d.get("kind") != KIND_TOOL_CALL:
        # Bu dilimde yürütülebilir tek tür tool_call. Diğer türler (§8.5:
        # tool_grant, agent_spec) onaylanır ama burada bir yan etkileri yoktur.
        logging.info("approvals: onaylandı id=%s kind=%s (yürütme yok)",
                     approval_id, d.get("kind"))
        return _result(STATUS_APPROVED, None, False)

    tool_name = d.get("tool_name")
    executor = (executors or {}).get(tool_name)
    if executor is None:
        # Allowlist (§6): kayıt defterinde olmayan isim ÇALIŞMAZ.
        ref.set({"status": STATUS_FAILED, "outcome": NO_EXECUTOR_OUTCOME}, merge=True)
        logging.warning("approvals: yürütücü yok id=%s tool=%s", approval_id, tool_name)
        return _result(STATUS_FAILED, NO_EXECUTOR_OUTCOME, False)

    try:
        outcome = executor(d.get("tool_args") or {}, user_id)
        status = STATUS_APPROVED
    except Exception as exc:
        # İlke 4 (hata = gözlem): hata modelden de Kadir'den de saklanmaz. Karar
        # VERİLMİŞ sayılır — ikinci bir onay bu eylemi tekrar DENEMEZ (§4.5).
        logging.exception("approvals: yürütme hatası id=%s tool=%s", approval_id, tool_name)
        outcome = f"yürütme hatası: {exc}"
        status = STATUS_FAILED

    ref.set({"status": status, "outcome": outcome}, merge=True)
    logging.info("approvals: karar id=%s status=%s by=%s", approval_id, status, user_id)
    return _result(status, outcome, False)


def expire_due(db, now_fn=_now) -> dict:
    """Süresi geçmiş `pending` onayları `expired`'a çeker, özet döner.

    Bir TEMİZLİK/bildirim yoludur, güvenlik sınırı DEĞİL: gerçek zaman aşımı
    garantisi decide()'ın süre kontrolündedir (§4.1). Bu iş hiç koşmasa bile
    süresi geçmiş bir onay çalıştırılamaz."""
    now = now_fn()
    parsed_now = _parse_iso(now)
    snaps = list(
        db.collection(COLLECTION)
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    expired = 0
    for snap in snaps:
        if not _is_expired(snap.to_dict(), parsed_now):
            continue
        snap.reference.set({"status": STATUS_EXPIRED, "decided_at": now,
                            "outcome": EXPIRED_OUTCOME}, merge=True)
        expired += 1
    summary = f"Onay turu: {len(snaps)} bekleyen onaydan {expired} tanesinin süresi doldu"
    logging.info("approvals: expire_due checked=%d expired=%d", len(snaps), expired)
    return {"expired": expired, "checked": len(snaps), "summary": summary}
