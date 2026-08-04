"""Cihaz token kayıt defteri (Wear W0, spec §4): saat gibi ikincil cihazların
KALICI, kayar süreli oturumu.

Google ID token'ı 1 saatte ölür ve tazelemesi Play Services ister; saat bu
yüzden telefona saat başı muhtaç kalırdı. Bunun yerine brain kendi token'ını
basar: Google kimliği yalnız BASIM anında kanıtlanır (require_google_user),
sonrası bu defterdir.

Dört değişmez:

1. **Düz token sunucuda yaşamaz.** Doküman kimliği düz token'ın SHA-256'sıdır;
   basım cevabı düz token'ın göründüğü tek yerdir. Log'a da yazılmaz.
2. **Fail-closed doğrulama.** Kayıt yok / revoked / süresi geçmiş / Firestore
   hatası — hepsi AYNI redde düşer: PermissionError("Geçersiz oturum").
   Google yoluyla aynı mesaj: hangi şemanın reddettiği dışarı sızmaz.
3. **Kayar süre.** Her başarılı doğrulama expires_at'i son kullanımdan
   SLIDING_DAYS sonrasına taşır: kullanılan token yaşar, 30 gün unutulan ölür.
   Mutlak üst sınır bilinçli olarak yok (tek kullanıcı, tek-istek iptal,
   last_used_at izi). Kayar yazım düşerse doğrulama yine geçer ama WARNING
   loglanır — sonuçsuz best-effort yasağı (4 Ağu FCM dersi).
4. **Revoke damgadır.** Kayıt silinmez; audit izi okunur kalır.

verify() PermissionError fırlatır (auth sözleşmesi); diğerleri gözlem/veri
döner. Basım yetkisi BURADA değil, uçtadır: cihaz token'ı token basamaz/iptal
edemez (require_google_user) — kimlik düzleminde recursion yasağı.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

COLLECTION = "device_tokens"
PREFIX = "jdt_"
SLIDING_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint(db, *, email: str, device: str, now_fn=_now, token_fn=None) -> dict:
    """Yeni cihaz token'ı basar ve YALNIZ hash'ini saklar.

    token_fn enjeksiyonu testler içindir; üretimde secrets.token_urlsafe."""
    plain = (token_fn or (lambda: PREFIX + secrets.token_urlsafe(32)))()
    token_id = _hash(plain)
    now = now_fn()
    expires = (_parse_iso(now) + timedelta(days=SLIDING_DAYS)).isoformat()
    db.collection(COLLECTION).document(token_id).set({
        "token_hash": token_id,
        "email": email,
        "device": device,
        "created_at": now,
        "last_used_at": now,
        "expires_at": expires,
        "revoked": False,
        "revoked_at": None,
    })
    logging.info("device_tokens: mint device=%s id=%s..", device, token_id[:12])
    return {"token": plain, "id": token_id, "device": device, "expires_at": expires}


def verify(db, token: str, *, now_fn=_now) -> str:
    """Token'ı doğrular, email döner; her ret AYNI PermissionError (değişmez 2).

    Başarıda kayar süreyi ilerletir (değişmez 3)."""
    try:
        snap = db.collection(COLLECTION).document(_hash(token)).get()
        doc = snap.to_dict() if snap.exists else None
    except Exception:
        logging.exception("device_tokens: doğrulama okuması düştü -- fail-closed")
        raise PermissionError("Geçersiz oturum")
    if not doc or doc.get("revoked"):
        raise PermissionError("Geçersiz oturum")
    now = _parse_iso(now_fn())
    expires = _parse_iso(doc.get("expires_at"))
    # Okunamayan damga DOLMUŞ sayılır (approvals._is_expired ile aynı kural).
    if now is None or expires is None or expires <= now:
        raise PermissionError("Geçersiz oturum")
    try:
        db.collection(COLLECTION).document(_hash(token)).set({
            "last_used_at": now.isoformat(),
            "expires_at": (now + timedelta(days=SLIDING_DAYS)).isoformat(),
        }, merge=True)
    except Exception:
        logging.warning("device_tokens: kayar süre güncellenemedi id=%s.. -- "
                        "doğrulama yine geçti", _hash(token)[:12])
    return doc["email"]


def list_tokens(db) -> list[dict]:
    """Kayıt listesi — düz token YOKTUR (zaten saklanmıyor, değişmez 1)."""
    rows = []
    for snap in db.collection(COLLECTION).stream():
        d = snap.to_dict()
        rows.append({"id": d.get("token_hash"), "device": d.get("device"),
                     "created_at": d.get("created_at"),
                     "last_used_at": d.get("last_used_at"),
                     "expires_at": d.get("expires_at"),
                     "revoked": bool(d.get("revoked"))})
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows


def revoke(db, token_id: str, *, now_fn=_now) -> str:
    """İptal: damga, silme yok (değişmez 4)."""
    ref = db.collection(COLLECTION).document(token_id)
    snap = ref.get()
    if not snap.exists:
        return f"'{token_id[:12]}..' cihaz token defterinde yok."
    ref.set({"revoked": True, "revoked_at": now_fn()}, merge=True)
    logging.info("device_tokens: revoke id=%s..", token_id[:12])
    return f"Cihaz token'ı iptal edildi ({snap.to_dict().get('device', '?')})."
