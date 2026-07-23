# Katman 2b — Backend Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Native Android chat'in kalıcı sohbet + bağlam ihtiyacını karşılamak için backend'e kalıcı mesaj deposu (`messages`), `GET /api/history` endpoint'i ve cold-start rehydration ekle.

**Architecture:** Uygulama-katmanı Firestore `messages` koleksiyonu (ADK session backend'ine bağımlı olmadan). `run_turn` her turu append eder; `/api/history` geçmişi döndürür; cold-start'ta `run_turn` geçmişi ADK oturumuna `append_event` ile geri yükler. Backend auth sözleşmesi ve mevcut endpoint'ler değişmez — yalnızca additive.

**Tech Stack:** Python / FastAPI, google-adk 1.36.2, google-cloud-firestore 2.28.0, pytest.

## Global Constraints

- **Tek veri deposu:** Firestore. Cloud SQL / yeni altyapı YOK (spec §12.2).
- **Auth sözleşmesi değişmez:** `Authorization: Bearer <Google ID token>`, `require_user` mevcut haliyle kullanılır (`app/auth.py`).
- **Firestore query API:** modern `FieldFilter` + `Query.DESCENDING`. Deprecated positional `.where("f","==",v)` KULLANMA.
- **Timestamp:** ISO 8601 string, `datetime.now(timezone.utc).isoformat()` — mevcut `memory.py::_now` deseniyle bire bir; mikrosaniye içerir. Testlerde sıra determinizmi için `now_fn` enjekte edilir (asla `sleep` ile flaky test yazma).
- **`MAX_HISTORY_MESSAGES = 100`** (≈50 tur; spec §8 "N=50 tur", 1 tur = user+model = 2 mesaj).
- **Rol değerleri:** `"user"` | `"model"` (Gemini/ADK content role'leriyle aynı — mapping yok).
- **Kullanıcı-yüzü metinler Türkçe, kod/identifier İngilizce.**
- **Testler:** pytest, `tests/fakes.py` deseni. Mevcut 70 test yeşil kalmalı; `./.venv/bin/pytest -q` ile çalıştır.
- **Her task sonunda commit**, trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Çalışma dizini:** pytest ve `gcloud run deploy` `brain/` içinden çalışır (önce `cd brain`); pytest binary `./.venv/bin/pytest`. `git add` yolları repo kökünden verilmiştir (`brain/...` önekli) — `brain/` içindeysen öneki düşür.

---

### Task 1: `MessageStore` + fake query desteği

Kalıcı mesaj deposu ve onu test edebilmek için `FakeCollection`'a Firestore query yüzeyi (where/order_by/limit).

**Files:**
- Create: `brain/app/messages.py`
- Modify: `brain/tests/fakes.py` (query zinciri ekle)
- Test: `brain/tests/test_messages.py`

**Interfaces:**
- Consumes: `tests/fakes.py::FakeDB`
- Produces:
  - `messages.MessageStore(db, now_fn: Callable[[], str] | None = None)`
  - `.append(user_id: str, session_id: str, role: str, text: str) -> None`
  - `.history(user_id: str, session_id: str, limit: int = 100) -> list[dict]` — kronolojik (eski→yeni), her öğe `{"role", "text", "ts"}`
  - `messages.sanitize_session_id(raw: str) -> str` — geçersizse `ValueError`
  - `messages.MAX_HISTORY_MESSAGES = 100`

- [ ] **Step 1: `tests/fakes.py`'ye query zinciri ekle**

`FakeSnap`/`FakeDoc`/`FakeCollection`/`FakeDB` mevcut. `FakeCollection`'a chainable query ekle:

```python
class FakeQuery:
    """Minimal Firestore query surface: where(filter=FieldFilter)/order_by/limit/stream."""
    def __init__(self, rows):
        self._rows = list(rows)          # list[dict]
        self._filters = []               # list[(field_path, op_string, value)]
        self._order = None               # (field, direction)
        self._limit = None

    def where(self, filter=None):
        self._filters.append((filter.field_path, filter.op_string, filter.value))
        return self

    def order_by(self, field, direction="ASCENDING"):
        self._order = (field, direction)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def stream(self):
        rows = [d for d in self._rows if self._match(d)]
        if self._order:
            field, direction = self._order
            rows.sort(key=lambda d: d.get(field), reverse=(direction == "DESCENDING"))
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnap(d) for d in rows]

    def _match(self, d):
        for field_path, op_string, value in self._filters:
            if op_string != "==":
                raise NotImplementedError(f"FakeQuery op {op_string}")
            if d.get(field_path) != value:
                return False
        return True
```

Ve `FakeCollection`'a bu üç metodu ekle (her biri yeni `FakeQuery` başlatır — Firestore'da `collection.where(...)` bir query döndürür):

```python
    def where(self, filter=None):
        return FakeQuery(self.docs.values()).where(filter=filter)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self.docs.values()).order_by(field, direction)

    def limit(self, n):
        return FakeQuery(self.docs.values()).limit(n)
```

- [ ] **Step 2: Başarısız testleri yaz**

`brain/tests/test_messages.py`:

```python
import pytest

from app.messages import MessageStore, sanitize_session_id
from tests.fakes import FakeDB


def _store():
    # Monotonic ISO-ish timestamps so ordering is deterministic (no sleep).
    counter = iter(f"2026-01-01T00:00:{i:02d}.000000+00:00" for i in range(60))
    return MessageStore(FakeDB(), now_fn=lambda: next(counter))


def test_append_and_history_roundtrip():
    store = _store()
    store.append("u@x.com", "s1", "user", "selam")
    store.append("u@x.com", "s1", "model", "merhaba")
    hist = store.history("u@x.com", "s1")
    assert [(h["role"], h["text"]) for h in hist] == [("user", "selam"), ("model", "merhaba")]


def test_history_isolates_by_user():
    store = _store()
    store.append("u@x.com", "s1", "user", "benim")
    store.append("other@x.com", "s1", "user", "baskasi")
    assert [h["text"] for h in store.history("u@x.com", "s1")] == ["benim"]


def test_history_isolates_by_session():
    store = _store()
    store.append("u@x.com", "s1", "user", "birinci")
    store.append("u@x.com", "s2", "user", "ikinci")
    assert [h["text"] for h in store.history("u@x.com", "s1")] == ["birinci"]


def test_history_returns_newest_within_limit_chronological():
    store = _store()
    for i in range(5):
        store.append("u@x.com", "s1", "user", f"m{i}")
    hist = store.history("u@x.com", "s1", limit=3)
    assert [h["text"] for h in hist] == ["m2", "m3", "m4"]  # newest 3, eski→yeni


def test_sanitize_session_id_accepts_uuid_like():
    assert sanitize_session_id("web-2026-07-24") == "web-2026-07-24"


@pytest.mark.parametrize("bad", ["", "  ", "a/b", "x" * 300, "a\nb"])
def test_sanitize_session_id_rejects_invalid(bad):
    with pytest.raises(ValueError):
        sanitize_session_id(bad)
```

- [ ] **Step 3: Testin başarısız olduğunu doğrula**

Run: `./.venv/bin/pytest tests/test_messages.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.messages'`

- [ ] **Step 4: `app/messages.py`'yi yaz**

```python
"""Persistent chat transcript store (Katman 2b) — Firestore `messages` collection.

Application-layer persistence, deliberately independent of ADK's session
backend (ADK 1.36.2 offers no Firestore-native SessionService; see spec §12).
Single responsibility: append turns and read them back, keyed by user+session.
"""
import re
from datetime import datetime, timezone
from typing import Callable

from google.cloud.firestore_v1 import Query
from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "messages"
MAX_HISTORY_MESSAGES = 100  # ~50 turns (1 turn = user + model)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_session_id(raw: str) -> str:
    """Return `raw` if it is a safe session id, else raise ValueError.

    Guards the client-supplied session_id before it reaches Firestore or gets
    used as a document key: allow only URL/id-safe chars, bounded length, no
    slashes/newlines/path tricks (spec §9 'session_id sanitization')."""
    if isinstance(raw, str) and _SESSION_ID_RE.match(raw):
        return raw
    raise ValueError("gecersiz session_id")


class MessageStore:
    def __init__(self, db, now_fn: Callable[[], str] | None = None):
        self.db = db
        self._now = now_fn or _now

    def append(self, user_id: str, session_id: str, role: str, text: str) -> None:
        self.db.collection(COLLECTION).add(
            {
                "user_id": user_id,
                "session_id": session_id,
                "role": role,
                "text": text,
                "ts": self._now(),
            }
        )

    def history(self, user_id: str, session_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
        """Newest `limit` messages for (user_id, session_id), returned oldest→newest.

        DESC + limit fetches the newest N (bounded read); we reverse to
        chronological so callers (UI render, rehydration replay) get natural
        order."""
        query = (
            self.db.collection(COLLECTION)
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("session_id", "==", session_id))
            .order_by("ts", direction=Query.DESCENDING)
            .limit(limit)
        )
        rows = [snap.to_dict() for snap in query.stream()]
        rows.reverse()
        return [{"role": r["role"], "text": r["text"], "ts": r["ts"]} for r in rows]
```

- [ ] **Step 5: Testin geçtiğini doğrula (ve tüm suite yeşil)**

Run: `./.venv/bin/pytest tests/test_messages.py -q && ./.venv/bin/pytest -q`
Expected: yeni testler PASS; toplam suite yeşil (70 + yeni testler).

- [ ] **Step 6: Commit**

```bash
git add brain/app/messages.py brain/tests/fakes.py brain/tests/test_messages.py
git commit -m "feat: persistent MessageStore + Firestore query fakes (Katman 2b)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `GET /api/history` endpoint

Client'ın açılışta geçmişi çekmesi için endpoint. `MessageStore`'u `main.py`'nin lazy-init'ine bağlar.

**Files:**
- Modify: `brain/app/main.py`
- Test: `brain/tests/test_api.py` (ekleme)

**Interfaces:**
- Consumes: `messages.MessageStore`, `messages.sanitize_session_id`, `messages.MAX_HISTORY_MESSAGES`, mevcut `require_user`
- Produces: `GET /api/history?session_id=<id>` → `{"messages": [{"role","text","ts"}, ...]}`; modül-global `main._messages: MessageStore | None`

- [ ] **Step 1: Başarısız testleri yaz**

`brain/tests/test_api.py`'ye ekle (dosya başındaki importlara `from app.messages import MessageStore` ve `from tests.fakes import FakeDB` ekle):

```python
def test_history_returns_user_session_messages(monkeypatch):
    store = MessageStore(FakeDB())
    store.append("owner@example.com", "s1", "user", "selam")
    store.append("owner@example.com", "s1", "model", "merhaba")
    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_init", lambda: None)  # skip Firestore/Runner init
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": "s1"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert [(m["role"], m["text"]) for m in r.json()["messages"]] == [
        ("user", "selam"), ("model", "merhaba")
    ]


def test_history_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.get("/api/history", params={"session_id": "s1"})
    assert r.status_code in (401, 403)


def test_history_rejects_bad_session_id(monkeypatch):
    monkeypatch.setattr(main_mod, "_messages", MessageStore(FakeDB()))
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.get("/api/history", params={"session_id": "a/b"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 400


def test_chat_rejects_bad_session_id():
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    try:
        with TestClient(main_mod.app) as c:
            r = c.post("/api/chat", json={"session_id": "a/b", "message": "selam"})
    finally:
        main_mod.app.dependency_overrides.clear()
    assert r.status_code == 400
```

- [ ] **Step 2: Testin başarısız olduğunu doğrula**

Run: `./.venv/bin/pytest tests/test_api.py -k history -q`
Expected: FAIL — 404 (endpoint yok) / AttributeError `_messages`.

- [ ] **Step 3: `main.py`'yi düzenle**

`main.py` başına import ekle:

```python
from . import config, messages, voice
```

`_init()` içinde `_messages`'i kur (mevcut `_memory`/`_runner` kurulumunun yanına). Global bildirime ekle ve init gövdesinde:

```python
_messages: "messages.MessageStore | None" = None
```

`_init()` gövdesinde (`global _runner, _memory` → `global _runner, _memory, _messages`), `db` oluşturulduktan sonra:

```python
    _messages = messages.MessageStore(db)
```

Endpoint'i `/api/chat`'in altına ekle:

```python
@app.get("/api/history")
async def history(session_id: str, email: str = Depends(require_user)):
    _init()
    try:
        sid = messages.sanitize_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz oturum kimliği")
    return {"messages": _messages.history(user_id=email, session_id=sid)}
```

Ayrıca mevcut `/api/chat` handler'ında `req.session_id`'yi `run_turn`'e vermeden önce sanitize et (spec §9 her iki endpoint için istiyor). Handler'ı tümüyle şu haline getir:

```python
@app.post("/api/chat")
async def chat(req: ChatRequest, email: str = Depends(require_user)):
    try:
        sid = messages.sanitize_session_id(req.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz oturum kimliği")
    try:
        reply = await run_turn(user_id=email, session_id=sid, message=req.message)
    except Exception:
        logging.exception("chat: run_turn failed for user_id=%s session_id=%s", email, sid)
        raise HTTPException(
            status_code=502,
            detail="Jarvis şu anda cevap veremiyor (altyapı hatası). Az sonra tekrar dene.",
        )
    return {"reply": reply}
```

- [ ] **Step 4: Testin geçtiğini doğrula**

Run: `./.venv/bin/pytest tests/test_api.py -q && ./.venv/bin/pytest -q`
Expected: history testleri PASS; suite yeşil.

- [ ] **Step 5: Commit**

```bash
git add brain/app/main.py brain/tests/test_api.py
git commit -m "feat: GET /api/history endpoint over MessageStore (Katman 2b)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `run_turn`'de kalıcı append (snapshot yerine)

Her chat turu artık `messages`'a yazılır; `run_turn` test edilebilir hale gelir.

**Files:**
- Modify: `brain/app/main.py` (`run_turn`)
- Test: `brain/tests/test_api.py` (ekleme) + `brain/tests/fakes.py` (`FakeRunner`)

**Interfaces:**
- Consumes: `main._messages`, `main._runner`, `main._session_service`
- Produces: `run_turn` davranışı — dönüşten önce `_messages.append(..., "user", message)` ve `_messages.append(..., "model", reply)`

- [ ] **Step 1: `tests/fakes.py`'ye `FakeRunner` ekle**

```python
class FakeEvent:
    def __init__(self, text):
        from types import SimpleNamespace
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])

    def is_final_response(self):
        return True


class FakeRunner:
    """Stands in for adk Runner: yields one final event echoing the message."""
    def __init__(self, reply="cevap"):
        self._reply = reply

    async def run_async(self, *, user_id, session_id, new_message):
        yield FakeEvent(self._reply)
```

- [ ] **Step 2: Başarısız testi yaz**

`brain/tests/test_api.py`'ye ekle (import: `from tests.fakes import FakeDB, FakeRunner`, `from app.messages import MessageStore`):

```python
@pytest.mark.asyncio
async def test_run_turn_persists_user_and_model_messages(monkeypatch):
    store = MessageStore(FakeDB())
    monkeypatch.setattr(main_mod, "_messages", store)
    monkeypatch.setattr(main_mod, "_runner", FakeRunner(reply="merhaba"))
    monkeypatch.setattr(main_mod, "_init", lambda: None)

    async def fake_ensure(user_id, session_id):
        return None  # session object unused by FakeRunner

    monkeypatch.setattr(main_mod, "_ensure_session", fake_ensure)

    reply = await main_mod.run_turn("u@x.com", "s1", "selam")
    assert reply == "merhaba"
    hist = store.history("u@x.com", "s1")
    assert [(h["role"], h["text"]) for h in hist] == [("user", "selam"), ("model", "merhaba")]
```

> Async testler: `pytest-asyncio` 1.4.0 kurulu, `asyncio_mode = "auto"` (`brain/pyproject.toml` → `[tool.pytest.ini_options]`). Mevcut `tests/test_voice.py` deseni her async teste `@pytest.mark.asyncio` koyar — bu testte de **aynısını** kullan (auto modda mark zorunlu değil ama proje deseni bu).

- [ ] **Step 3: Testin başarısız olduğunu doğrula**

Run: `./.venv/bin/pytest tests/test_api.py -k persists -q`
Expected: FAIL — `_ensure_session` yok / append yapılmıyor.

- [ ] **Step 4: `run_turn`'ü düzenle**

`run_turn`'deki mevcut session get-or-create bloğunu `_ensure_session` çağrısıyla değiştir (bu fonksiyon Task 4'te doldurulacak; şimdilik mevcut mantığı taşıyan bir stub olarak ekle) ve append'leri ekle. `snapshot_session` çağrısını kaldır:

```python
async def _ensure_session(user_id: str, session_id: str):
    """Get the ADK session or create it. Rehydration eklenene kadar (Task 4)
    yalnızca get-or-create yapar."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        session = await _session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    return session


async def run_turn(user_id: str, session_id: str, message: str) -> str:
    _init()
    await _ensure_session(user_id, session_id)
    _messages.append(user_id, session_id, "user", message)
    content = types.Content(role="user", parts=[types.Part(text=message)])
    reply = ""
    async for event in _runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = event.content.parts[0].text or ""
    _messages.append(user_id, session_id, "model", reply)
    return reply
```

> Not: eski gövdedeki `_memory.snapshot_session(...)` satırı KALDIRILDI (transcript artık `messages`'ta). `_memory.snapshot_session` **metodu** silinmez — voice tarafı (2a) hâlâ kullanıyor.

- [ ] **Step 5: Testin geçtiğini + tüm suite'in yeşil olduğunu doğrula**

Run: `./.venv/bin/pytest -q`
Expected: yeni test PASS; **mevcut `test_api.py` chat testleri hâlâ yeşil** (run_turn imzası değişmedi).

- [ ] **Step 6: Commit**

```bash
git add brain/app/main.py brain/tests/test_api.py brain/tests/fakes.py
git commit -m "feat: persist chat turns to MessageStore in run_turn (Katman 2b)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Cold-start rehydration

Cold-start'ta (yeni instance → boş `InMemorySessionService`) ADK oturumu `messages` geçmişinden `append_event` ile geri yüklenir; Jarvis bağlamı korunur.

**Files:**
- Modify: `brain/app/main.py` (`_ensure_session`)
- Test: `brain/tests/test_api.py` (ekleme)

**Interfaces:**
- Consumes: `main._session_service` (`InMemorySessionService`), `main._messages`, ADK `Event`, `types.Content`
- Produces: `_ensure_session` — session yeni oluşturulduğunda `messages.history()`'yi `append_event` ile oturuma yükler

**ADK gerçekleri (doğrulandı):** `Event(author: str, content: Content)` — `author` zorunlu. `InMemorySessionService.create_session(*, app_name, user_id, session_id)` ve `append_event(session, event)` async. `Session.events: list[Event]`.

- [ ] **Step 1: Başarısız testi yaz**

`brain/tests/test_api.py`'ye ekle (import: `from google.adk.sessions import InMemorySessionService`):

```python
@pytest.mark.asyncio
async def test_ensure_session_rehydrates_from_history(monkeypatch):
    store = MessageStore(FakeDB())
    store.append("u@x.com", "s1", "user", "adim Kadir")
    store.append("u@x.com", "s1", "model", "memnun oldum Kadir")
    svc = InMemorySessionService()
    monkeypatch.setattr(main_mod, "_session_service", svc)
    monkeypatch.setattr(main_mod, "_messages", store)

    session = await main_mod._ensure_session("u@x.com", "s1")
    texts = [ev.content.parts[0].text for ev in session.events]
    assert texts == ["adim Kadir", "memnun oldum Kadir"]
    roles = [ev.content.role for ev in session.events]
    assert roles == ["user", "model"]


@pytest.mark.asyncio
async def test_ensure_session_existing_is_not_rehydrated(monkeypatch):
    store = MessageStore(FakeDB())
    store.append("u@x.com", "s1", "user", "eski")
    svc = InMemorySessionService()
    await svc.create_session(app_name=main_mod.APP_NAME, user_id="u@x.com", session_id="s1")
    monkeypatch.setattr(main_mod, "_session_service", svc)
    monkeypatch.setattr(main_mod, "_messages", store)

    session = await main_mod._ensure_session("u@x.com", "s1")
    assert session.events == []  # zaten vardı → geçmişten doldurulmaz
```

- [ ] **Step 2: Testin başarısız olduğunu doğrula**

Run: `./.venv/bin/pytest tests/test_api.py -k rehydrat -q`
Expected: FAIL — `_ensure_session` geçmişi yüklemiyor (events boş).

- [ ] **Step 3: `_ensure_session`'ı rehydration ile güncelle**

`main.py` başına import: `from google.adk.events import Event`. `_ensure_session`'ı değiştir:

```python
async def _ensure_session(user_id: str, session_id: str):
    """Get the ADK session, or create it and rehydrate from the persistent
    transcript so a cold-started instance keeps conversational context."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is not None:
        return session
    session = await _session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    for msg in _messages.history(user_id, session_id):
        event = Event(
            author=msg["role"],  # "user" | "model"
            content=types.Content(
                role=msg["role"], parts=[types.Part(text=msg["text"])]
            ),
        )
        await _session_service.append_event(session, event)
    logging.info(
        "rehydrate: user=%s session=%s events=%d",
        user_id, session_id, len(session.events),
    )
    return session
```

> DATA-log (`rehydrate: ... events=N`) spec §9 gereği: bir cold-start bağlam sorunu tek çalıştırmada lokalize olur.

- [ ] **Step 4: Testin geçtiğini + suite'in yeşil olduğunu doğrula**

Run: `./.venv/bin/pytest -q`
Expected: rehydration testleri PASS; suite yeşil.

- [ ] **Step 5: Commit**

```bash
git add brain/app/main.py brain/tests/test_api.py
git commit -m "feat: cold-start session rehydration from MessageStore (Katman 2b)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Firestore index + deploy + prod smoke

`messages` sorgusu composite index gerektirir (eşitlik ×2 + order_by). Index'i sürüm kontrollü tanımla, deploy et, canlı doğrula.

**Files:**
- Create: `brain/firestore.indexes.json`
- Modify: `brain/README.md` (deploy notuna index adımı) — opsiyonel

**Interfaces:** Yok (altyapı task'ı).

- [ ] **Step 1: Index tanımını yaz**

`brain/firestore.indexes.json`:

```json
{
  "indexes": [
    {
      "collectionGroup": "messages",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "user_id", "order": "ASCENDING" },
        { "fieldPath": "session_id", "order": "ASCENDING" },
        { "fieldPath": "ts", "order": "DESCENDING" }
      ]
    }
  ],
  "fieldOverrides": []
}
```

- [ ] **Step 2: Index'i oluştur (gcloud)**

Run:
```bash
gcloud firestore indexes composite create \
  --project your-gcp-project \
  --collection-group messages \
  --query-scope COLLECTION \
  --field-config field-path=user_id,order=ascending \
  --field-config field-path=session_id,order=ascending \
  --field-config field-path=ts,order=descending
```
Expected: "Waiting for [...] to finish" → index CREATING→READY (birkaç dk). Zaten varsa `ALREADY_EXISTS` — sorun değil.

- [ ] **Step 3: Backend'i deploy et**

Run (spec §Deploy / mevcut komut):
```bash
gcloud run deploy jarvis-brain --source . --region europe-west1 \
  --project your-gcp-project --allow-unauthenticated \
  --set-secrets GOOGLE_API_KEY=gemini-api-key:latest \
  --set-env-vars JARVIS_OAUTH_CLIENT_ID=000000000000-tmu4im1mba53dmqj1gbhgba55v3i6hmb.apps.googleusercontent.com
```
Expected: deploy başarılı, yeni revision serving.

- [ ] **Step 4: Prod smoke — history endpoint canlı**

Gerçek bir Google ID token gerektirir (Kadir'den). Token elde edilebilirse:
```bash
curl -s -H "Authorization: Bearer <ID_TOKEN>" \
  "https://jarvis-brain-000000000000.europe-west1.run.app/api/history?session_id=web-2026-07-24" | head
```
Expected: `{"messages": [...]}` (chat sonrası dolu; boşsa `{"messages": []}`), 200. Token yoksa bu adım HITL olarak işaretlenir ve Android planında gerçek client ile doğrulanır.

- [ ] **Step 5: Commit**

```bash
git add brain/firestore.indexes.json brain/README.md
git commit -m "chore: messages composite index + deploy notes (Katman 2b)" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notlar (sonraki plana devir)

- Android client bu planın ürettiği `POST /api/chat` + `GET /api/history` sözleşmesine karşı geliştirilecek (ayrı plan: `2026-07-24-katman-2b-android-chat.md`).
- Rehydration `author` alanı basitçe role ("user"/"model") kullanır — attribution için yeterli; ADK yalnızca `content.role`'ü LLM bağlamında kullanır.
- Voice tarafının `messages`'a taşınması (tek transcript kaynağı) sonraki ses dilimi işi; bu planda voice `snapshot_session`'a dokunulmadı.
