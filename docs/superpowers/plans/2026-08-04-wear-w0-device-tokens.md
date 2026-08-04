# Wear W0 — Cihaz Token Altyapısı Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brain'e kalıcı, kayar süreli cihaz token'ları: bas / doğrula / iptal — saatin Google'a ve telefona muhtaç olmadan kimliklenmesi (spec: `docs/superpowers/specs/2026-08-04-wear-os-design.md` §4).

**Architecture:** Yeni `brain/app/device_tokens.py` modülü (hash-only Firestore `device_tokens` koleksiyonu); `auth.py`'de ortak kapı `verify_bearer_email` (önek dallanması: `jdt_` → kayıt defteri, değilse Google) + `require_google_user` (token token yönetemez); `main.py`'ye 3 REST ucu; ses WS handshake'i ortak kapıya bağlanır; `guest_gate` BİLİNÇLİ olarak Google-yalnız kalır.

**Tech Stack:** Python 3.12, FastAPI, Firestore (FakeDB testte), pytest (asyncio auto), `secrets`/`hashlib` stdlib.

## Global Constraints

- **Düz token sunucuda ASLA saklanmaz/loglanmaz** — yalnız SHA-256 hex (doc id). Mint cevabı düz token'ın göründüğü TEK yer.
- Token biçimi: `jdt_` + `secrets.token_urlsafe(32)`. `PREFIX = "jdt_"`, `SLIDING_DAYS = 30`, `COLLECTION = "device_tokens"`.
- **Doğrulama fail-closed:** kayıt yok / revoked / süresi geçmiş / Firestore hatası → `PermissionError("Geçersiz oturum")` — Google yoluyla AYNI mesaj (hangi şemanın reddettiği sızmaz).
- **Kayar süre:** başarılı doğrulamada `last_used_at=now`, `expires_at=now+30gün`. Güncelleme yazımı düşerse doğrulama YİNE geçer ama `logging.warning` şart (sonuçsuz best-effort yasağı).
- **Basım/iptal/listeleme yalnız taze Google ID token'la** (`require_google_user`): `jdt_` önekli Authorization bu uçlarda 403 alır.
- `guest_gate.py`'ye DOKUNULMAZ (MCP kapısı Google-yalnız — bilinçli, plan bunu test ile pinler).
- Docstring/kullanıcı metinleri Türkçe, tanımlayıcılar/commit İngilizce.
- Testler: `cd /home/user/Projeler/JARVIS/brain && python -m pytest <dosya> -q` (venv: `.venv/bin/python`); commit repo kökünden, mesaj sonu:
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: `device_tokens` modülü

**Files:**
- Create: `brain/app/device_tokens.py`
- Test: `brain/tests/test_device_tokens.py`

**Interfaces:**
- Consumes: stdlib (`secrets`, `hashlib`), Firestore db (FakeDB testte).
- Produces (sonraki görevler bunlara dayanır):
  - `COLLECTION = "device_tokens"`, `PREFIX = "jdt_"`, `SLIDING_DAYS = 30`
  - `mint(db, *, email, device, now_fn=_now, token_fn=None) -> dict` — `{"token": <düz>, "id": <hash>, "device", "expires_at"}`
  - `verify(db, token, *, now_fn=_now) -> str` — email döner; her ret `PermissionError("Geçersiz oturum")`
  - `list_tokens(db) -> list[dict]` — düz token içermez
  - `revoke(db, token_id, *, now_fn=_now) -> str` — Türkçe gözlem

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_device_tokens.py`:

```python
"""Wear W0 — cihaz token kayıt defteri (spec §4).

Taşıyıcı pimler:
- Düz token Firestore'a ASLA yazılmaz (yalnız SHA-256 doc id).
- verify fail-closed: yok/revoked/süresi geçmiş/DB hatası → PermissionError,
  mesaj Google yoluyla AYNI ("Geçersiz oturum") — şema sızıntısı yok.
- Kayar süre: her başarılı doğrulama expires_at'i İLERLETİR.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app import device_tokens
from tests.fakes import FakeDB

EMAIL = "owner@example.com"
T0 = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)


def _now_at(dt):
    return lambda: dt.isoformat()


def _mint(db, *, at=T0, device="watch-ultra"):
    return device_tokens.mint(db, email=EMAIL, device=device, now_fn=_now_at(at))


def test_mint_returns_plain_token_and_stores_only_the_hash():
    db = FakeDB()
    out = _mint(db)
    assert out["token"].startswith(device_tokens.PREFIX)
    expected_id = hashlib.sha256(out["token"].encode()).hexdigest()
    assert out["id"] == expected_id
    docs = db.collection(device_tokens.COLLECTION).docs
    assert list(docs) == [expected_id]
    stored = str(docs[expected_id])
    assert out["token"] not in stored          # düz token dokümanın HİÇBİR alanında yok
    assert docs[expected_id]["email"] == EMAIL
    assert docs[expected_id]["device"] == "watch-ultra"
    assert docs[expected_id]["revoked"] is False


def test_mint_sets_sliding_expiry_30_days_out():
    db = FakeDB()
    out = _mint(db)
    doc = db.collection(device_tokens.COLLECTION).docs[out["id"]]
    assert doc["expires_at"] == (T0 + timedelta(days=30)).isoformat()
    assert out["expires_at"] == doc["expires_at"]


def test_verify_returns_email_and_slides_the_expiry():
    db = FakeDB()
    out = _mint(db)
    later = T0 + timedelta(days=10)
    email = device_tokens.verify(db, out["token"], now_fn=_now_at(later))
    assert email == EMAIL
    doc = db.collection(device_tokens.COLLECTION).docs[out["id"]]
    assert doc["last_used_at"] == later.isoformat()
    assert doc["expires_at"] == (later + timedelta(days=30)).isoformat()  # İLERLEDİ


def test_verify_rejects_unknown_revoked_and_expired():
    db = FakeDB()
    out = _mint(db)

    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, "jdt_uydurma", now_fn=_now_at(T0))

    device_tokens.revoke(db, out["id"], now_fn=_now_at(T0))
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, out["token"], now_fn=_now_at(T0))

    out2 = _mint(db)
    stale = T0 + timedelta(days=31)
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, out2["token"], now_fn=_now_at(stale))


def test_verify_is_fail_closed_on_db_error():
    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(BoomDB(), "jdt_herhangi", now_fn=_now_at(T0))


def test_verify_still_passes_when_the_sliding_update_write_fails(caplog):
    """Kayar güncelleme düşerse doğrulama GEÇER ama WARNING şart (spec §4.3)."""
    db = FakeDB()
    out = _mint(db)

    real_collection = db.collection

    class ReadOnlyDoc:
        def __init__(self, inner):
            self._inner = inner

        def get(self):
            return self._inner.get()

        def set(self, *a, **k):
            raise RuntimeError("write denied")

    class Wrapper:
        def __init__(self, col):
            self._col = col

        def document(self, key):
            return ReadOnlyDoc(self._col.document(key))

    email = device_tokens.verify(
        Wrapper(real_collection(device_tokens.COLLECTION)), out["token"],
        now_fn=_now_at(T0 + timedelta(days=1)))
    assert email == EMAIL
    assert any("kayar" in r.message.lower() or "slid" in r.message.lower()
               or "güncelle" in r.message.lower()
               for r in caplog.records if r.levelname == "WARNING")


def test_expired_boundary_is_closed():
    """expires_at == now TAM ANINDA token ölüdür (approvals._is_expired kuralı)."""
    db = FakeDB()
    out = _mint(db)
    exactly = T0 + timedelta(days=30)
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        device_tokens.verify(db, out["token"], now_fn=_now_at(exactly))


def test_list_tokens_never_contains_plain_tokens():
    db = FakeDB()
    out = _mint(db)
    _mint(db, device="tablet")
    rows = device_tokens.list_tokens(db)
    assert len(rows) == 2
    joined = str(rows)
    assert out["token"] not in joined
    assert {r["device"] for r in rows} == {"watch-ultra", "tablet"}
    assert all(set(r) >= {"id", "device", "created_at", "last_used_at",
                          "expires_at", "revoked"} for r in rows)


def test_revoke_stamps_without_deleting_and_unknown_is_observation():
    db = FakeDB()
    out = _mint(db)
    msg = device_tokens.revoke(db, out["id"], now_fn=_now_at(T0))
    doc = db.collection(device_tokens.COLLECTION).docs[out["id"]]
    assert doc["revoked"] is True and doc["revoked_at"] == T0.isoformat()
    assert "iptal" in msg.lower()
    assert "yok" in device_tokens.revoke(db, "olmayan-id", now_fn=_now_at(T0)).lower()
```

- [ ] **Step 2: FAIL gör**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_device_tokens.py -q`
Expected: `ModuleNotFoundError: app.device_tokens`

- [ ] **Step 3: Modülü yaz** — `brain/app/device_tokens.py`:

```python
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
```

- [ ] **Step 4: PASS gör + tam süit**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_device_tokens.py -q && .venv/bin/python -m pytest -q`
Expected: tümü yeşil (782 + ~9 yeni)

- [ ] **Step 5: Commit**

```bash
git add brain/app/device_tokens.py brain/tests/test_device_tokens.py
git commit -m "feat(auth): device token registry — durable sliding-window watch sessions

Hash-only storage, fail-closed verify with the same rejection text as the
Google path, sliding 30-day expiry that logs when the slide write fails.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `auth` ortak kapısı + ses WS bağlaması

**Files:**
- Modify: `brain/app/auth.py` (yeni: `init`, `verify_bearer_email`, `require_google_user`; `require_user` ortak kapıya döner)
- Modify: `brain/app/voice.py:42,509` (import + çağrı `verify_bearer_email`e döner)
- Modify: `brain/app/main.py` (`_init` içinde `auth.init(lambda: _memory.db)` — `_memory = Memory(...)` atamasının hemen ardına)
- Test: `brain/tests/test_auth_gate.py` (yeni)

**Interfaces:**
- Consumes: Task 1'in `device_tokens.verify` + `PREFIX`'i.
- Produces:
  - `auth.init(db_provider: Callable[[], object]) -> None`
  - `auth.verify_bearer_email(token: str) -> str` — ortak kapı (HTTP + WS)
  - `auth.require_google_user(authorization) -> str` — `jdt_` reddeden FastAPI dependency (Task 3 uçları bunu kullanır)

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_auth_gate.py`:

```python
"""Wear W0 — ortak kimlik kapısı (spec §4.3).

Taşıyıcı pimler:
- jdt_ öneki kayıt defterine, gerisi Google'a gider; iki yol birbirine sızmaz.
- Cihaz token'ı token YÖNETEMEZ (require_google_user 403).
- Ses WS handshake'i ortak kapının TA KENDİSİNİ kullanır (isim pini).
"""
import pytest

import app.auth as auth_mod
import app.voice as voice_mod
from app import device_tokens
from app.memory import Memory
from tests.fakes import FakeDB

EMAIL = "owner@example.com"


@pytest.fixture()
def db(monkeypatch):
    db = FakeDB()
    auth_mod.init(lambda: db)
    yield db
    auth_mod.init(None)


def _mint(db):
    return device_tokens.mint(db, email=EMAIL, device="watch-ultra")


def test_a_device_token_verifies_through_the_common_gate(db):
    out = _mint(db)
    assert auth_mod.verify_bearer_email(out["token"]) == EMAIL


def test_a_google_token_still_goes_to_google(db, monkeypatch):
    seen = {}

    def fake_google(token):
        seen["token"] = token
        return EMAIL

    monkeypatch.setattr(auth_mod, "verify_token_email", fake_google)
    assert auth_mod.verify_bearer_email("google-jwt") == EMAIL
    assert seen["token"] == "google-jwt"


def test_an_uninitialised_gate_fails_closed_for_device_tokens(monkeypatch):
    auth_mod.init(None)
    with pytest.raises(PermissionError, match="Geçersiz oturum"):
        auth_mod.verify_bearer_email("jdt_herhangi")


def test_require_user_accepts_a_device_token(db):
    out = _mint(db)
    assert auth_mod.require_user(f"Bearer {out['token']}") == EMAIL


def test_require_google_user_rejects_device_tokens_with_403(db):
    out = _mint(db)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        auth_mod.require_google_user(f"Bearer {out['token']}")
    assert exc.value.status_code == 403


def test_require_google_user_passes_google_tokens(db, monkeypatch):
    monkeypatch.setattr(auth_mod, "verify_token_email", lambda t: EMAIL)
    assert auth_mod.require_google_user("Bearer google-jwt") == EMAIL


def test_the_voice_handshake_uses_the_common_gate():
    """İsim pini: voice.py ortak kapıyı import eder — cihaz token'ı ses WS'ini
    bedavaya açar (spec §4.3). Bu pin düşerse saat sesli oturumdan sessizce
    dışlanmış demektir."""
    assert voice_mod.verify_bearer_email is auth_mod.verify_bearer_email


def test_guest_gate_stays_google_only():
    """BİLİNÇLİ sınır (plan Global Constraints): MCP misafir kapısı cihaz
    token'ı TANIMAZ."""
    import app.guest_gate as guest_mod

    assert getattr(guest_mod, "verify_token_email", None) is auth_mod.verify_token_email
    assert not hasattr(guest_mod, "verify_bearer_email")
```

- [ ] **Step 2: FAIL gör**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_auth_gate.py -q`
Expected: `AttributeError: ... init` / `verify_bearer_email`

- [ ] **Step 3: Uygula**

`brain/app/auth.py` — dosyanın sonuna (mevcut fonksiyonlar DEĞİŞMEDEN, yalnız `require_user` gövdesi ortak kapıya döner):

```python
# -- Wear W0: cihaz token'ları (spec §4.3) -----------------------------------
#
# verify_bearer_email iki şemanın ORTAK kapısıdır: `jdt_` önekli token cihaz
# kayıt defterine, gerisi Google'a gider. HTTP (require_user) ve ses WS
# handshake'i (voice.py) aynı kapıyı kullanır. db, modül import zinciri
# döngüsüz kalsın diye provider ile enjekte edilir: main._init başlangıçta
# auth.init(lambda: _memory.db) çağırır; None/başarısız provider cihaz
# token'ını FAIL-CLOSED reddeder, Google yolu etkilenmez.

_db_provider = None


def init(db_provider) -> None:
    global _db_provider
    _db_provider = db_provider


def verify_bearer_email(token: str) -> str:
    """Ortak kimlik kapısı: cihaz token'ı veya Google ID token → email.

    Her iki yol da aynı PermissionError mesajını kullanır ("Geçersiz oturum") —
    hangi şemanın reddettiği dışarı sızmaz."""
    from . import device_tokens

    if token.startswith(device_tokens.PREFIX):
        if _db_provider is None:
            raise PermissionError("Geçersiz oturum")
        try:
            db = _db_provider()
        except Exception:
            raise PermissionError("Geçersiz oturum")
        return device_tokens.verify(db, token)
    return verify_token_email(token)


def require_google_user(authorization: str = Header(default="")) -> str:
    """Yalnız TAZE Google ID token kabul eden dependency (spec §4.2).

    Cihaz token uçları (bas/listele/iptal) bunu kullanır: cihaz token'ı kendi
    soyunu yönetemez — kimlik düzleminde recursion yasağı."""
    from . import device_tokens

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    token = authorization.removeprefix("Bearer ")
    if token.startswith(device_tokens.PREFIX):
        raise HTTPException(status_code=403,
                            detail="Bu işlem için Google oturumu gerekli")
    try:
        return verify_token_email(token)
    except PermissionError as exc:
        status_code = 401 if str(exc) == "Geçersiz oturum" else 403
        raise HTTPException(status_code=status_code, detail=str(exc))
```

`require_user` gövdesinde tek satır değişir: `return verify_token_email(token)` → `return verify_bearer_email(token)` (docstring'ine bir cümle: "Ortak kapı: Google ID token VEYA `jdt_` cihaz token'ı — spec §4.3").

`brain/app/voice.py`: satır 42 `from .auth import verify_token_email` → `from .auth import verify_bearer_email`; satır 509 `email = verify_token_email(parsed["token"])` → `email = verify_bearer_email(parsed["token"])`.

`brain/app/main.py` `_init` içinde `_memory = Memory(...)` atamasının hemen ardına:

```python
    # Wear W0: cihaz token doğrulaması aynı Firestore istemcisini kullanır
    # (auth modülü main'i import edemez -- provider enjeksiyonu, spec §4.3).
    auth.init(lambda: _memory.db)
```

(`main.py` başında `from . import ...` bloğuna `auth` zaten import ediliyorsa dokunma; yalnız `from .auth import require_scheduler, require_user` var — bloğa `from . import auth` ekle ya da mevcut import stilini izleyerek `auth.init`e erişilebilir kıl.)

- [ ] **Step 4: PASS gör + tam süit**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_auth_gate.py tests/test_voice.py -q && .venv/bin/python -m pytest -q`
Expected: tümü yeşil — özellikle test_voice değişmeden geçmeli (handshake davranışı Google token'larla birebir aynı).

- [ ] **Step 5: Commit**

```bash
git add brain/app/auth.py brain/app/voice.py brain/app/main.py brain/tests/test_auth_gate.py
git commit -m "feat(auth): common bearer gate — device tokens beside Google ID tokens

jdt_-prefixed tokens verify against the registry, everything else stays on
the Google path; voice WS handshake wired to the same gate; guest gate
deliberately Google-only (pinned).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: REST uçları

**Files:**
- Modify: `brain/app/main.py` (3 yeni uç — mevcut `/api/fcm/register` ucunun yakınına, aynı stil)
- Test: `brain/tests/test_device_tokens_api.py` (yeni)

**Interfaces:**
- Consumes: Task 1 `device_tokens.mint/list_tokens/revoke`; Task 2 `auth.require_google_user`.
- Produces: `POST /api/device-tokens` (`{device}` → 200 `{token, id, device, expires_at}`), `GET /api/device-tokens` (→ `{tokens: [...]}`), `DELETE /api/device-tokens/{token_id}` (→ `{ok: true, message}`); üçü de `Depends(require_google_user)`.

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_device_tokens_api.py`:

```python
"""Wear W0 — cihaz token uçları (spec §4.2). test_approvals_api client deseni."""
import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.main as main_mod
from app import device_tokens
from app.auth import require_google_user
from app.memory import Memory
from app.messages import MessageStore
from tests.fakes import FakeDB

USER = "owner@example.com"


@pytest.fixture()
def db():
    return FakeDB()


@pytest.fixture()
def client(monkeypatch, db):
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_messages", MessageStore(db))
    auth_mod.init(lambda: db)
    main_mod.app.dependency_overrides[require_google_user] = lambda: USER
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()
    auth_mod.init(None)


def test_endpoints_require_auth():
    with TestClient(main_mod.app) as c:
        assert c.post("/api/device-tokens", json={"device": "w"}).status_code in (401, 403)
        assert c.get("/api/device-tokens").status_code in (401, 403)
        assert c.delete("/api/device-tokens/x").status_code in (401, 403)


def test_a_device_token_cannot_manage_tokens(client, db):
    """Cihaz token'ı token basamaz/listeleyemez/iptal edemez (spec §4.2)."""
    out = device_tokens.mint(db, email=USER, device="watch-ultra")
    main_mod.app.dependency_overrides.clear()      # gerçek require_google_user koşsun
    headers = {"Authorization": f"Bearer {out['token']}"}
    assert client.post("/api/device-tokens", json={"device": "w"},
                       headers=headers).status_code == 403
    assert client.get("/api/device-tokens", headers=headers).status_code == 403
    assert client.delete(f"/api/device-tokens/{out['id']}",
                         headers=headers).status_code == 403


def test_mint_list_revoke_roundtrip(client, db):
    r = client.post("/api/device-tokens", json={"device": "watch-ultra"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"].startswith(device_tokens.PREFIX)
    assert body["device"] == "watch-ultra"

    listed = client.get("/api/device-tokens").json()["tokens"]
    assert [t["device"] for t in listed] == ["watch-ultra"]
    assert body["token"] not in str(listed)        # düz token listede YOK

    r2 = client.delete(f"/api/device-tokens/{body['id']}")
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert client.get("/api/device-tokens").json()["tokens"][0]["revoked"] is True


def test_minted_token_authenticates_chat_and_dies_on_revoke(client, db, monkeypatch):
    """ZİNCİR PİNİ (spec §10 W0): mint → require_user geçer → revoke → 401.
    /api/chat yerine daha ucuz bir require_user ucu kullanılır: /api/approvals."""
    body = client.post("/api/device-tokens", json={"device": "watch-ultra"}).json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/approvals", headers=headers).status_code == 200
    client.delete(f"/api/device-tokens/{body['id']}")
    assert client.get("/api/approvals", headers=headers).status_code == 401


def test_mint_rejects_a_blank_device(client):
    assert client.post("/api/device-tokens", json={"device": "  "}).status_code == 422
```

- [ ] **Step 2: FAIL gör**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_device_tokens_api.py -q`
Expected: 404'ler (uçlar yok)

- [ ] **Step 3: Uçları yaz** — `brain/app/main.py`, `/api/fcm/register` ucunun altına (import bloğuna `device_tokens` ekle; `require_google_user` importunu `from .auth import ...` satırına ekle):

```python
class DeviceTokenRequest(BaseModel):
    device: str = Field(min_length=1)

    @field_validator("device")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("device boş olamaz")
        return v


@app.post("/api/device-tokens")
async def mint_device_token(req: DeviceTokenRequest,
                            email: str = Depends(require_google_user)):
    """Kalıcı cihaz token'ı basar (Wear W0, spec §4.2). Yalnız TAZE Google
    oturumuyla: cihaz token'ı kendi soyunu yönetemez. Düz token YALNIZ bu
    cevapta görünür."""
    return device_tokens.mint(_memory.db, email=email, device=req.device)


@app.get("/api/device-tokens")
async def list_device_tokens(email: str = Depends(require_google_user)):
    return {"tokens": device_tokens.list_tokens(_memory.db)}


@app.delete("/api/device-tokens/{token_id}")
async def revoke_device_token(token_id: str,
                              email: str = Depends(require_google_user)):
    message = device_tokens.revoke(_memory.db, token_id)
    return {"ok": True, "message": message}
```

(`BaseModel`/`Field`/`field_validator` importları dosyada zaten hangi biçimdeyse ona uy; `pydantic` v2 stili mevcutsa `field_validator` kullan, değilse mevcut istek modellerinin doğrulama desenini birebir kopyala.)

- [ ] **Step 4: PASS gör + tam süit**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_device_tokens_api.py -q && .venv/bin/python -m pytest -q`
Expected: tümü yeşil

- [ ] **Step 5: Commit**

```bash
git add brain/app/main.py brain/tests/test_device_tokens_api.py
git commit -m "feat(api): device-token endpoints — mint/list/revoke, Google-fresh only

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Deploy + W0 kanıtı

**Files:** yok (deploy + doğrulama)

- [ ] **Step 1: Tam süit** — `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest -q` → tümü yeşil
- [ ] **Step 2: İmaj + deploy** — `gcloud builds submit --tag gcr.io/your-gcp-project/jarvis-brain:w0-<sha7> brain/ --project your-gcp-project`; iki YAML'da tag güncelle + commit; `gcloud run services replace` (brain + voice), `--region europe-west1`.
- [ ] **Step 3: Doğrulama (3 Ağu standardı):**

```bash
# trafik latest'te ve imaj doğru mu
gcloud run services describe jarvis-brain --region europe-west1 --project your-gcp-project \
  --format="value(status.traffic.list(),spec.template.spec.containers[0].image)"
# yol envanteri 21 -> 23 (3 operasyon, 2 yeni path anahtarı)
curl -s https://jarvis-brain-000000000000.europe-west1.run.app/openapi.json | \
  python -c "import json,sys; print(len(json.load(sys.stdin)['paths']))"
# negatif kontrol: uydurma cihaz token'ı 401
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer jdt_uydurma" \
  https://jarvis-brain-000000000000.europe-west1.run.app/api/approvals
# Google yolu regresyonsuz: Kadir'in telefonu çalışmaya devam ediyor (sohbet aç/kapa)
```

- [ ] **Step 4: Kapanış** — mesh checkpoint + `jarvis-durum.md` güncelle. Gerçek mint→chat→revoke zinciri W1'in eşleştirme akışında kanıtlanacak (spec §10).

---

## Self-Review Notları

- **Spec kapsaması:** §4.1→Task 1, §4.2→Task 3, §4.3→Task 2, §4.4 dengeler dokümante, §10 W0→Task 4. Boşluk yok.
- **Tip tutarlılığı:** `verify` PermissionError fırlatır (auth sözleşmesi); mint/list/revoke veri/gözlem döner; `now_fn` her yerde ISO-string döndüren callable (approvals deseni).
- **Bilinen riskler:** (1) `main.py` istek-modeli doğrulama stili (pydantic v1/v2) dosyadan doğrulanacak — Task 3 Step 3 notu; (2) `test_voice.py`'nin handshake testleri monkeypatch'i `verify_token_email` üzerinden yapıyorsa Task 2'nin importu onları kırabilir — implementer test_voice'un handshake bölümünü OKUYUP mevcut monkeypatch hedefini korumalı (voice artık `verify_bearer_email` import ettiği için testler `voice_mod.verify_bearer_email`i yamamalı; kırılan varsa AYNI davranışı koruyarak güncelle ve raporda belirt); (3) `dependency_overrides[require_google_user]` — FastAPI dependency'nin fonksiyon NESNESİ route tanımında kullanılanla aynı olmalı (import yolu birebir: `from app.auth import require_google_user`).
