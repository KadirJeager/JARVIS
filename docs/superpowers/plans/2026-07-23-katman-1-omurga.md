# Katman 1 — Omurga Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cloud Run üzerinde ADK tabanlı Jarvis beyni: politika katmanı + 3 kademeli hafıza iskeleti + Firestore audit log + Google girişli Web/PWA sohbet istemcisi. "Bitti" ölçütü: Kadir kendi uygulamasından yazışıyor, Jarvis onu hatırlıyor, her eylem loglanıyor.

**Architecture:** Tek Cloud Run servisi (`jarvis-brain`): FastAPI, ADK `Runner` + `InMemorySessionService` ile orkestratör ajanı koşar; her turdan sonra oturum özeti Firestore'a snapshot'lanır (Kademe 2). Tüm araç çağrıları `before_tool_callback` üzerinden politika katmanından geçer ve `audit_log`'a yazılır. Web/PWA istemcisi aynı servisten statik sunulur; kimlik Google Sign-In (ID token) + e-posta allowlist.

**Tech Stack:** Python 3.12, google-adk (>=1.16,<2.0), FastAPI, google-cloud-firestore, google-auth, google-genai (embeddings, Task 10), vanilla JS PWA. GCP: Cloud Run, Firestore (Native), Secret Manager. Model: AI Studio üzerinden `gemini-flash-latest` (kural: daima `-latest` alias'ları — `gemini-flash-latest`/`gemini-pro-latest`; sabit sürüm pinlenmez).

## Global Constraints

- GCP projesi: `your-gcp-project`, hesap `owner@example.com`, bölge `europe-west1`, Firestore konumu `eur3`.
- Kod, tanımlayıcılar ve yorumlar İngilizce; kullanıcıya görünen tüm metinler (ajan talimatı, UI, hata mesajları) Türkçe.
- İlke 1: istemcide sıfır zeka — PWA yalnızca API'yi çağırır ve render eder.
- İlke 6/§9: bilinmeyen araç = KIRMIZI bölge (güvenli varsayılan); her politika kararı audit log'a yazılır.
- İlke 9: scale-to-zero — `min-instances=0`, sürekli çalışan hiçbir şey yok.
- Sırlar asla repoya girmez: API anahtarı yalnızca Secret Manager'da; OAuth client ID sır değildir, env ile geçilir.
- `google-adk>=1.16,<2.0` (2.0 alpha'ya geçilmez); Python 3.12.
- Her görev sonunda commit; commit mesajları İngilizce, `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` ile biter.

---

### Task 1: Repo iskeleti

**Files:**
- Create: `.gitignore`, `README.md`, `docs/JARVIS_Proje_Belgesi.md` (mevcut belgenin kopyası)

**Interfaces:**
- Produces: git repo kökü `/home/user/Projeler/JARVIS`; North Star belgesi `docs/` altında.

- [ ] **Step 1: Git repo başlat ve iskeleti kur**

```bash
cd /home/user/Projeler/JARVIS
git init -b main
mkdir -p docs brain/app brain/web brain/tests
cp JARVIS_Proje_Belgesi.md docs/JARVIS_Proje_Belgesi.md
```

- [ ] **Step 2: .gitignore yaz**

```gitignore
__pycache__/
*.pyc
.venv/
.env
*.egg-info/
.pytest_cache/
.claude/
```

- [ ] **Step 3: README.md yaz**

```markdown
# JARVIS

Kişisel otonom asistan. Nihai hedef mimari: `docs/JARVIS_Proje_Belgesi.md` (North Star).
Bu repo Katman 1'den (Omurga) itibaren o hedefe doğru büyür.

- `brain/` — Cloud Run servisi: ADK orkestratör + politika katmanı + hafıza + Web/PWA istemci
- Uygulama planları: `docs/superpowers/plans/`
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: repo skeleton with North Star document"
```

---

### Task 2: GCP hazırlığı (API'ler, Firestore, sırlar) — HITL adımları içerir

**Files:** yok (yalnızca gcloud komutları + konsol adımları)

**Interfaces:**
- Produces: etkin API'ler; `(default)` Firestore DB (Native, eur3); Secret Manager'da `gemini-api-key`; OAuth Web Client ID (Kadir'den, env için).

- [ ] **Step 1: Gerekli API'leri etkinleştir**

```bash
gcloud services enable run.googleapis.com firestore.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com cloudresourcemanager.googleapis.com \
  --project your-gcp-project
```
Expected: `Operation ... finished successfully.` (birkaç dakika sürebilir)

- [ ] **Step 2: Firestore veritabanını oluştur**

```bash
gcloud firestore databases create --location=eur3 --type=firestore-native --project your-gcp-project
```
Expected: çıktıda `name: projects/your-gcp-project/databases/(default)`

- [ ] **Step 3 (HITL — Kadir): AI Studio API anahtarı**

Kadir https://aistudio.google.com/apikey adresinden `your-gcp-project` projesine bağlı bir API anahtarı üretir. Anahtar chat'e yapıştırılmaz; doğrudan şu komuta girilir:

```bash
printf '%s' 'ANAHTAR_BURAYA' | gcloud secrets create gemini-api-key --data-file=- --project your-gcp-project
```
Expected: `Created version [1] of the secret [gemini-api-key].`

- [ ] **Step 4 (HITL — Kadir): OAuth Web Client ID**

Konsol → APIs & Services → Credentials → Create Credentials → OAuth client ID → Web application. Authorized JavaScript origins: `http://localhost:8080` (Cloud Run URL'i Task 9'da eklenecek). Üretilen client ID (`....apps.googleusercontent.com`) sır değildir; not edilir, Task 7/9'da env olarak kullanılır.

- [ ] **Step 5: Doğrulama**

```bash
gcloud services list --enabled --project your-gcp-project | grep -E "run|firestore|secretmanager"
gcloud secrets versions list gemini-api-key --project your-gcp-project
```
Expected: üç API listede; secret'ta `1  enabled` satırı.

---

### Task 3: Python paketi ve test altyapısı

**Files:**
- Create: `brain/pyproject.toml`, `brain/app/__init__.py`, `brain/tests/__init__.py`, `brain/tests/test_sanity.py`

**Interfaces:**
- Produces: `pip install -e ".[dev]"` ile kurulan `app` paketi; çalışan pytest.

- [ ] **Step 1: pyproject.toml yaz**

```toml
[project]
name = "jarvis-brain"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "google-adk>=1.16,<2.0",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "google-cloud-firestore>=2.16",
    "google-auth>=2.30",
    "google-genai>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["app"]
```

- [ ] **Step 2: Boş paket + sanity testi yaz**

`brain/app/__init__.py` ve `brain/tests/__init__.py` boş. `brain/tests/test_sanity.py`:

```python
def test_sanity():
    assert True
```

- [ ] **Step 3: Kur ve testi çalıştır**

```bash
cd /home/user/Projeler/JARVIS/brain
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```
Expected: `1 passed`

- [ ] **Step 4: Commit**

```bash
git add brain/pyproject.toml brain/app/__init__.py brain/tests/
git commit -m "chore: python package and test scaffolding for brain service"
```

---

### Task 4: Politika katmanı (Eylem Yetki Matrisi + audit)

**Files:**
- Create: `brain/app/config.py`, `brain/app/policy.py`
- Test: `brain/tests/test_policy.py`

**Interfaces:**
- Produces: `config.TOOL_ZONES: dict[str,str]`, `config.DEFAULT_ZONE`, sabitler `ZONE_GREEN/ZONE_YELLOW/ZONE_RED`; `policy.check_zone(tool_name: str) -> str`; `policy.make_policy_callback(audit_writer) -> Callable` (ADK `before_tool_callback` imzasıyla uyumlu: `(tool, args, tool_context) -> dict | None`); `policy.AuditWriter` protokolü: `write(entry: dict) -> None`.
- Consumes: yok.

- [ ] **Step 1: Failing testleri yaz** (`brain/tests/test_policy.py`)

```python
from types import SimpleNamespace
from app import config
from app.policy import check_zone, make_policy_callback


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def _tool(name):
    return SimpleNamespace(name=name)


def test_known_tool_zone():
    assert check_zone("get_user_profile") == config.ZONE_GREEN


def test_unknown_tool_defaults_to_red():
    assert check_zone("launch_missiles") == config.ZONE_RED


def test_green_tool_allowed_and_audited():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {"a": 1}, None)
    assert result is None  # None => ADK aracı gerçekten çalıştırır
    assert audit.entries[0]["decision"] == "allow"
    assert audit.entries[0]["zone"] == config.ZONE_GREEN


def test_red_tool_blocked_with_turkish_message():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, None)
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["decision"] == "block"


def test_dry_run_intercepts_all_tools(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {"x": 2}, None)
    assert result is not None and "DRY-RUN" in result["result"]
    assert audit.entries[0]["decision"] == "dry_run"
```

- [ ] **Step 2: Çalıştır, FAIL gör**

```bash
.venv/bin/pytest tests/test_policy.py -q
```
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: config.py yaz**

```python
import os

MODEL_NAME = os.environ.get("JARVIS_MODEL", "gemini-2.5-flash")
DRY_RUN = os.environ.get("JARVIS_DRY_RUN", "0") == "1"
OAUTH_CLIENT_ID = os.environ.get("JARVIS_OAUTH_CLIENT_ID", "")
ALLOWED_EMAILS = set(
    filter(None, os.environ.get("JARVIS_ALLOWED_EMAILS", "owner@example.com").split(","))
)

# Eylem Yetki Matrisi (North Star §9) — tool name -> zone
ZONE_GREEN = "green"
ZONE_YELLOW = "yellow"
ZONE_RED = "red"

TOOL_ZONES = {
    "get_user_profile": ZONE_GREEN,
    "search_memory": ZONE_GREEN,
    "remember_fact": ZONE_GREEN,
    "add_lesson": ZONE_GREEN,
    "update_user_profile": ZONE_YELLOW,
}
DEFAULT_ZONE = ZONE_RED  # unknown tool = red (safe default, §9)
```

- [ ] **Step 4: policy.py yaz**

```python
"""Action authorization matrix (North Star §9): every tool call passes here."""
from datetime import datetime, timezone
from typing import Any, Protocol

from . import config


class AuditWriter(Protocol):
    def write(self, entry: dict) -> None: ...


def check_zone(tool_name: str) -> str:
    return config.TOOL_ZONES.get(tool_name, config.DEFAULT_ZONE)


def make_policy_callback(audit: AuditWriter):
    def policy_callback(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
        zone = check_zone(tool.name)
        if zone == config.ZONE_RED:
            decision = "block"
        elif config.DRY_RUN:
            decision = "dry_run"
        else:
            decision = "allow"
        audit.write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "orchestrator",
            "tool": tool.name,
            "args": {k: str(v)[:500] for k, v in (args or {}).items()},
            "zone": zone,
            "decision": decision,
        })
        if decision == "block":
            return {"result": (
                f"POLİTİKA ENGELİ: '{tool.name}' kırmızı bölgede — onaysız çalıştırılamaz. "
                "Kadir'e ne yapmak istediğini söyle ve onay iste."
            )}
        if decision == "dry_run":
            return {"result": f"DRY-RUN: '{tool.name}' şu argümanlarla çalışacaktı: {args}"}
        return None

    return policy_callback
```

- [ ] **Step 5: Testleri çalıştır, PASS gör**

```bash
.venv/bin/pytest tests/test_policy.py -q
```
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add brain/app/config.py brain/app/policy.py brain/tests/test_policy.py
git commit -m "feat: policy layer with zone matrix, safe-default red, dry-run and audit"
```

---

### Task 5: Hafıza katmanı (profil, ders defteri, oturum snapshot)

**Files:**
- Create: `brain/app/memory.py`, `brain/tests/fakes.py`
- Test: `brain/tests/test_memory.py`

**Interfaces:**
- Consumes: yok (Firestore client dependency-injection ile gelir).
- Produces: `memory.Memory(db)` sınıfı — `get_profile() -> dict`, `update_profile(patch: dict) -> dict`, `remember_fact(fact: str) -> str`, `add_lesson(context: str, tried: str, went_wrong: str, correct: str) -> str`, `search_memory(query: str, top_k: int = 5) -> list[dict]`, `snapshot_session(session_id: str, user_id: str, summary: dict) -> None`; `memory.FirestoreAudit(db)` — `write(entry)` (Task 4'teki `AuditWriter` uyumlu). Firestore koleksiyonları: `profile/main`, `facts`, `lessons`, `sessions`, `audit_log`.
- Not: Katman 1'de `search_memory` basit "son kayıtlar + alt-dizgi eşleşmesi"dir; semantik arama Task 10'da eklenir.

- [ ] **Step 1: Fake Firestore yaz** (`brain/tests/fakes.py`)

```python
"""Minimal in-memory stand-in for the Firestore client surface Memory uses."""
import itertools


class FakeDoc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self):
        data = self.store.get(self.key)
        return type("Snap", (), {"exists": data is not None, "to_dict": lambda s=None, d=data: dict(d or {})})()

    def set(self, data, merge=False):
        if merge and self.key in self.store:
            self.store[self.key].update(data)
        else:
            self.store[self.key] = dict(data)


class FakeCollection:
    _ids = itertools.count()

    def __init__(self):
        self.docs = {}

    def document(self, doc_id=None):
        doc_id = doc_id or f"auto{next(self._ids)}"
        return FakeDoc(self.docs, doc_id)

    def add(self, data):
        doc_id = f"auto{next(self._ids)}"
        self.docs[doc_id] = dict(data)
        return None, FakeDoc(self.docs, doc_id)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())
```

- [ ] **Step 2: Failing testleri yaz** (`brain/tests/test_memory.py`)

```python
from app.memory import FirestoreAudit, Memory
from tests.fakes import FakeDB


def test_profile_roundtrip():
    m = Memory(FakeDB())
    assert m.get_profile() == {}
    m.update_profile({"name": "Kadir", "coffee": "X kafeden"})
    assert m.get_profile()["coffee"] == "X kafeden"


def test_remember_fact_and_search():
    m = Memory(FakeDB())
    m.remember_fact("Kadir salı akşamları aranmak istemiyor")
    hits = m.search_memory("salı")
    assert len(hits) == 1 and "salı" in hits[0]["text"]


def test_add_lesson_shape():
    db = FakeDB()
    m = Memory(db)
    m.add_lesson("sipariş", "eski akış", "buton değişmiş", "yeni akış şu")
    lessons = list(db.collection("lessons").docs.values())
    assert lessons[0]["went_wrong"] == "buton değişmiş"


def test_session_snapshot_written():
    db = FakeDB()
    m = Memory(db)
    m.snapshot_session("s1", "kadir", {"turns": 3, "summary": "tanışma"})
    assert db.collection("sessions").docs["s1"]["summary"] == "tanışma"


def test_firestore_audit_writes_to_audit_log():
    db = FakeDB()
    FirestoreAudit(db).write({"tool": "x", "decision": "allow"})
    assert list(db.collection("audit_log").docs.values())[0]["decision"] == "allow"
```

- [ ] **Step 3: Çalıştır, FAIL gör**

```bash
.venv/bin/pytest tests/test_memory.py -q
```
Expected: `ModuleNotFoundError: No module named 'app.memory'`

- [ ] **Step 4: memory.py yaz**

```python
"""Tiered memory skeleton (North Star §4.5, §8): profile, facts, lessons, session snapshots."""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Memory:
    def __init__(self, db):
        self.db = db

    # -- Kademe 3: user profile (§8.1)
    def get_profile(self) -> dict:
        snap = self.db.collection("profile").document("main").get()
        return snap.to_dict() if snap.exists else {}

    def update_profile(self, patch: dict) -> dict:
        self.db.collection("profile").document("main").set(patch, merge=True)
        return self.get_profile()

    # -- Kademe 3: explicit facts / preferences
    def remember_fact(self, fact: str) -> str:
        self.db.collection("facts").add({"text": fact, "ts": _now()})
        return "kaydedildi"

    # -- Kademe 3: lesson log (§8.2)
    def add_lesson(self, context: str, tried: str, went_wrong: str, correct: str) -> str:
        self.db.collection("lessons").add({
            "context": context, "tried": tried,
            "went_wrong": went_wrong, "correct": correct, "ts": _now(),
        })
        return "ders kaydedildi"

    # -- Katman 1 search: substring over facts+lessons (semantic upgrade: Task 10)
    def search_memory(self, query: str, top_k: int = 5) -> list[dict]:
        q = query.lower()
        hits = []
        for name in ("facts", "lessons"):
            for data in self.db.collection(name).docs.values() if hasattr(
                self.db.collection(name), "docs"
            ) else []:
                text = " ".join(str(v) for v in data.values())
                if q in text.lower():
                    hits.append({"source": name, "text": text})
        return hits[:top_k]

    # -- Kademe 2: session snapshot (§4.5)
    def snapshot_session(self, session_id: str, user_id: str, summary: dict) -> None:
        self.db.collection("sessions").document(session_id).set(
            {"user_id": user_id, "ts": _now(), **summary}
        )


class FirestoreAudit:
    def __init__(self, db):
        self.db = db

    def write(self, entry: dict) -> None:
        self.db.collection("audit_log").add(entry)
```

**DİKKAT — bilinen sınır:** `search_memory` fake DB'nin `docs` sözlüğünü kullanıyor; gerçek Firestore'da `collection.stream()` gerekir. Implementasyonda şu satır kullanılır: `for snap in self.db.collection(name).stream(): data = snap.to_dict()`. FakeCollection'a `stream()` ekle (docs değerlerini `to_dict`'li snap objeleri olarak döndürsün) ve memory.py'de yalnızca `stream()` kullan — çift yol bırakma. Test yeşil kalmalı.

- [ ] **Step 5: Testleri çalıştır, PASS gör**

```bash
.venv/bin/pytest tests/test_memory.py -q
```
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add brain/app/memory.py brain/tests/fakes.py brain/tests/test_memory.py
git commit -m "feat: tiered memory skeleton (profile, facts, lessons, snapshots, audit)"
```

---

### Task 6: Araçlar ve orkestratör ajanı

**Files:**
- Create: `brain/app/tools.py`, `brain/app/agent.py`
- Test: `brain/tests/test_tools.py`

**Interfaces:**
- Consumes: `Memory` (Task 5), `make_policy_callback` + `FirestoreAudit` (Task 4/5), `config.MODEL_NAME`.
- Produces: `tools.init(memory: Memory) -> None` (modül-global memory bağlar); araç fonksiyonları `get_user_profile()`, `update_user_profile(patch: dict)`, `remember_fact(fact: str)`, `add_lesson(context, tried, went_wrong, correct)`, `search_memory(query: str)`; `agent.build_agent(memory: Memory, audit) -> Agent` (ADK `Agent`, `before_tool_callback` bağlı).

- [ ] **Step 1: Failing test yaz** (`brain/tests/test_tools.py`)

```python
from app import tools
from app.agent import build_agent
from app.memory import Memory
from tests.fakes import FakeDB


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def test_tools_delegate_to_memory():
    tools.init(Memory(FakeDB()))
    tools.remember_fact("test gerçeği")
    assert tools.search_memory("gerçeği")[0]["text"].startswith("test")
    assert tools.get_user_profile() == {}


def test_agent_wires_tools_and_policy():
    agent = build_agent(Memory(FakeDB()), FakeAudit())
    tool_names = {getattr(t, "__name__", getattr(t, "name", "")) for t in agent.tools}
    assert "get_user_profile" in tool_names
    assert agent.before_tool_callback is not None
    assert "Jarvis" in agent.instruction
```

- [ ] **Step 2: Çalıştır, FAIL gör**

```bash
.venv/bin/pytest tests/test_tools.py -q
```
Expected: `ModuleNotFoundError: No module named 'app.tools'`

- [ ] **Step 3: tools.py yaz**

```python
"""Agent-facing tool functions. Docstrings are the LLM's tool descriptions (Turkish)."""
from .memory import Memory

_memory: Memory | None = None


def init(memory: Memory) -> None:
    global _memory
    _memory = memory


def get_user_profile() -> dict:
    """Kadir'in profilini (tercihler, rutinler, kurallar) getirir. Her oturumun başında çağır."""
    return _memory.get_profile()


def update_user_profile(patch: dict) -> dict:
    """Kadir'in profiline kalıcı bilgi ekler/günceller. Örn: {"coffee": "X kafeden"}."""
    return _memory.update_profile(patch)


def remember_fact(fact: str) -> str:
    """Kadir hakkında öğrenilen tek bir gerçeği kalıcı hafızaya yazar."""
    return _memory.remember_fact(fact)


def add_lesson(context: str, tried: str, went_wrong: str, correct: str) -> str:
    """Bir hata düzeltildiğinde ders kaydeder: bağlam, ne denendi, ne yanlış gitti, doğrusu ne."""
    return _memory.add_lesson(context, tried, went_wrong, correct)


def search_memory(query: str) -> list[dict]:
    """Kalıcı hafızada (gerçekler + dersler) arama yapar."""
    return _memory.search_memory(query)


ALL_TOOLS = [get_user_profile, update_user_profile, remember_fact, add_lesson, search_memory]
```

- [ ] **Step 4: agent.py yaz**

```python
from google.adk.agents import Agent

from . import config, tools
from .memory import Memory
from .policy import make_policy_callback

INSTRUCTION = """Sen Jarvis'sin — Kadir'in kişisel asistanı. Kendini her zaman \
"Kadir'in asistanı Jarvis" olarak tanıtırsın; Kadir'in yerine geçmezsin.

Davranış kuralları:
- Her oturumun başında get_user_profile aracını çağırıp Kadir'i tanı; cevaplarını profile göre kişiselleştir.
- Kadir bir tercih, rutin veya kural söylerse ("bundan sonra şöyle yap", "ben X'i severim") \
bunu remember_fact veya update_user_profile ile ANINDA kalıcılaştır ve kalıcılaştırdığını söyle.
- Bir hatan düzeltilirse add_lesson ile ders kaydet; benzer görevlerden önce search_memory ile geçmiş dersleri ara.
- Bir araç politika engeline takılırsa bunu Kadir'den saklama; ne yapmak istediğini ve neden \
engellendiğini açıkça söyle (hata = gözlem ilkesi).
- Türkçe konuş; samimi ama profesyonel ol."""


def build_agent(memory: Memory, audit) -> Agent:
    tools.init(memory)
    return Agent(
        name="jarvis_orchestrator",
        model=config.MODEL_NAME,
        instruction=INSTRUCTION,
        tools=tools.ALL_TOOLS,
        before_tool_callback=make_policy_callback(audit),
    )
```

- [ ] **Step 5: Testleri çalıştır, PASS gör**

```bash
.venv/bin/pytest tests/test_tools.py -q
```
Expected: `2 passed`. Not: `Agent(...)` kurulumu ağ çağrısı yapmaz; model adı yalnızca metadata'dır, test API anahtarı istemez. Eğer `agent.tools` üzerindeki isim kontrolü ADK'nın sürümüne göre farklı sarmalama yüzünden düşerse, test `len(agent.tools) == 5` kontrolüne gevşetilir — davranış Task 8'deki canlı smoke ile zaten doğrulanacak.

- [ ] **Step 6: Commit**

```bash
git add brain/app/tools.py brain/app/agent.py brain/tests/test_tools.py
git commit -m "feat: memory tools and orchestrator agent with policy callback"
```

---

### Task 7: Kimlik doğrulama + Chat API

**Files:**
- Create: `brain/app/auth.py`, `brain/app/main.py`
- Test: `brain/tests/test_api.py`

**Interfaces:**
- Consumes: `build_agent` (Task 6), `Memory`/`FirestoreAudit` (Task 5), `config.OAUTH_CLIENT_ID`, `config.ALLOWED_EMAILS`.
- Produces: FastAPI `app.main:app`; `POST /api/chat` gövde `{"session_id": str, "message": str}` → `{"reply": str}`; `GET /healthz` → `{"status":"ok"}`; `auth.require_user` dependency (Bearer ID token → e-posta döner, değilse 401/403). Statik dosyalar `/` altında (Task 8'de dolar).

- [ ] **Step 1: Failing testleri yaz** (`brain/tests/test_api.py`)

```python
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.auth import require_user


@pytest.fixture()
def client(monkeypatch):
    async def fake_reply(user_id, session_id, message):
        return f"echo:{message}"

    monkeypatch.setattr(main_mod, "run_turn", fake_reply)
    main_mod.app.dependency_overrides[require_user] = lambda: "owner@example.com"
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_roundtrip(client):
    r = client.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:selam"


def test_chat_requires_auth():
    with TestClient(main_mod.app) as c:
        r = c.post("/api/chat", json={"session_id": "s1", "message": "selam"})
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Çalıştır, FAIL gör**

```bash
.venv/bin/pytest tests/test_api.py -q
```
Expected: `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: auth.py yaz**

```python
"""Google Sign-In ID token verification + email allowlist (North Star §5 public gates)."""
from fastapi import Header, HTTPException
from google.auth.transport import requests as grequests
from google.oauth2 import id_token

from . import config


def require_user(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    token = authorization.removeprefix("Bearer ")
    try:
        info = id_token.verify_oauth2_token(token, grequests.Request(), config.OAUTH_CLIENT_ID)
    except ValueError:
        raise HTTPException(status_code=401, detail="Geçersiz oturum")
    email = info.get("email", "")
    if email not in config.ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail="Bu hesap yetkili değil")
    return email
```

- [ ] **Step 4: main.py yaz**

```python
import os

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from .auth import require_user

APP_NAME = "jarvis"
app = FastAPI(title="JARVIS Brain")

_runner: Runner | None = None
_session_service = InMemorySessionService()
_memory = None


def _init() -> None:
    """Lazy init so tests can import the module without GCP credentials."""
    global _runner, _memory
    if _runner is not None:
        return
    from google.cloud import firestore

    from .agent import build_agent
    from .memory import FirestoreAudit, Memory

    db = firestore.Client()
    _memory = Memory(db)
    _runner = Runner(
        app_name=APP_NAME,
        agent=build_agent(_memory, FirestoreAudit(db)),
        session_service=_session_service,
    )


async def run_turn(user_id: str, session_id: str, message: str) -> str:
    _init()
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        session = await _session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    content = types.Content(role="user", parts=[types.Part(text=message)])
    reply = ""
    async for event in _runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = event.content.parts[0].text or ""
    _memory.snapshot_session(session_id, user_id, {"last_message": message, "last_reply": reply})
    return reply


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest, email: str = Depends(require_user)):
    reply = await run_turn(user_id=email, session_id=req.session_id, message=req.message)
    return {"reply": reply}


_web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
```

**DİKKAT:** Test, `main_mod.run_turn`'ü monkeypatch'liyor ama endpoint modül fonksiyonunu doğrudan çağırıyor — Python'da `main.py` içindeki `chat` fonksiyonu modül global'ini okuduğu için monkeypatch çalışır (`main_mod.run_turn = fake` aynı global'i değiştirir). Endpoint içinde `await run_turn(...)` yerine `await globals()["run_turn"](...)` YAZMA; mevcut hali doğru.

- [ ] **Step 5: Testleri çalıştır, PASS gör**

```bash
.venv/bin/pytest tests/test_api.py -q && .venv/bin/pytest -q
```
Expected: `3 passed`, ardından tüm suite yeşil (toplam 15+ test).

- [ ] **Step 6: Commit**

```bash
git add brain/app/auth.py brain/app/main.py brain/tests/test_api.py
git commit -m "feat: authenticated chat API over ADK runner with session snapshots"
```

---

### Task 8: Web/PWA istemci + yerel uçtan uca smoke

**Files:**
- Create: `brain/web/index.html`, `brain/web/app.js`, `brain/web/manifest.json`

**Interfaces:**
- Consumes: `POST /api/chat` sözleşmesi (Task 7), Google Identity Services (GIS) client ID.
- Produces: `/` altında sohbet UI'ı; `window.JARVIS_CLIENT_ID` global'i `index.html`'de tanımlı.

- [ ] **Step 1: index.html yaz**

> **Bilinçli güvenlik kararı — SRI yok:** `accounts.google.com/gsi/client` betiğine `integrity=` HASH'İ EKLENMEZ. Bu betik Google tarafından sürekli güncellenen birinci taraf bir kaynaktır; hash sabitlenirse Google'ın her güncellemesinde giriş kırılır (Google'ın resmi dokümantasyonu da SRI'siz yükler). Bu, üçüncü taraf CDN'den kütüphane çekme durumu değildir — sayfada başka hiçbir dış betik olmayacak.

```html
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Jarvis</title>
  <link rel="manifest" href="/manifest.json">
  <script src="https://accounts.google.com/gsi/client" async></script>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; font-family: system-ui, sans-serif; background: #101418; color: #e8eaed;
           display: flex; flex-direction: column; height: 100dvh; }
    #log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
    .msg { max-width: 85%; padding: 10px 14px; border-radius: 16px; white-space: pre-wrap; }
    .me { align-self: flex-end; background: #2b4c7e; }
    .jarvis { align-self: flex-start; background: #1f2937; }
    form { display: flex; gap: 8px; padding: 12px; border-top: 1px solid #2a2f36; }
    input { flex: 1; padding: 12px; border-radius: 24px; border: 1px solid #2a2f36;
            background: #181c22; color: inherit; font-size: 16px; }
    button { padding: 12px 18px; border-radius: 24px; border: 0; background: #2b4c7e; color: #fff; }
    #signin { display: grid; place-items: center; flex: 1; }
  </style>
</head>
<body>
  <script>window.JARVIS_CLIENT_ID = "CLIENT_ID_BURAYA.apps.googleusercontent.com";</script>
  <div id="signin"><div id="gbtn"></div></div>
  <div id="log" hidden></div>
  <form id="f" hidden>
    <input id="inp" placeholder="Jarvis'e yaz..." autocomplete="off">
    <button>Gönder</button>
  </form>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: app.js yaz**

```javascript
let idToken = null;
const sessionId = "web-" + new Date().toISOString().slice(0, 10);

window.onload = () => {
  google.accounts.id.initialize({
    client_id: window.JARVIS_CLIENT_ID,
    callback: (resp) => {
      idToken = resp.credential;
      document.getElementById("signin").hidden = true;
      document.getElementById("log").hidden = false;
      document.getElementById("f").hidden = false;
    },
  });
  google.accounts.id.renderButton(document.getElementById("gbtn"), { theme: "filled_black" });
};

function addMsg(text, cls) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  const log = document.getElementById("log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = document.getElementById("inp");
  const message = inp.value.trim();
  if (!message) return;
  inp.value = "";
  addMsg(message, "me");
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + idToken },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    addMsg((await r.json()).reply, "jarvis");
  } catch (err) {
    addMsg("Hata: " + err.message + " (oturum süresi dolduysa sayfayı yenile)", "jarvis");
  }
});
```

- [ ] **Step 3: manifest.json yaz**

```json
{
  "name": "Jarvis",
  "short_name": "Jarvis",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#101418",
  "theme_color": "#101418",
  "icons": []
}
```

- [ ] **Step 4: index.html'e gerçek client ID'yi koy**

Task 2 Step 4'te üretilen client ID `CLIENT_ID_BURAYA...` yerine yazılır (sır değildir, repoya girebilir).

- [ ] **Step 5: Güncel model adını doğrula ve yerel smoke**

```bash
export GOOGLE_API_KEY="$(gcloud secrets versions access latest --secret gemini-api-key --project your-gcp-project)"
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=${GOOGLE_API_KEY}" | grep -o '"name": "models/gemini[^"]*"' | sort -u | head -20
```
Expected: kullanılabilir model listesi. `gemini-2.5-flash` listede yoksa, listedeki güncel flash modelini seç ve `JARVIS_MODEL` env'ine yaz (config.py varsayılanını da güncelle).

```bash
cd /home/user/Projeler/JARVIS/brain
gcloud auth application-default login   # yerel Firestore erişimi için (HITL, bir kez)
JARVIS_OAUTH_CLIENT_ID="CLIENT_ID" .venv/bin/uvicorn app.main:app --port 8080
```
Tarayıcıda `http://localhost:8080`: Google ile gir → "Selam, ben Kadir. Kahveyi hep X'ten söylerim" yaz → cevap gelmeli. Ardından **yeni bir session** (sayfayı gizli pencerede aç) → "kahveyi nereden söylüyorum?" → profil/facts üzerinden hatırlamalı. Firestore konsolunda `audit_log` ve `sessions` koleksiyonlarında kayıt görülmeli.

- [ ] **Step 6: Commit**

```bash
git add brain/web/
git commit -m "feat: PWA chat client with Google Sign-In"
```

---

### Task 9: Dockerfile + Cloud Run deploy + prod smoke

**Files:**
- Create: `brain/Dockerfile`, `brain/.dockerignore`

**Interfaces:**
- Consumes: tüm servis (Task 3-8), `gemini-api-key` secret'ı (Task 2).
- Produces: `https://jarvis-brain-....run.app` canlı URL.

- [ ] **Step 1: Dockerfile yaz**

```dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY web ./web
RUN pip install --no-cache-dir .
ENV PORT=8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

`.dockerignore`:

```
.venv
tests
__pycache__
```

- [ ] **Step 2: Deploy**

```bash
cd /home/user/Projeler/JARVIS/brain
gcloud run deploy jarvis-brain --source . --region europe-west1 --project your-gcp-project \
  --allow-unauthenticated --min-instances 0 --memory 512Mi \
  --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest" \
  --set-env-vars "JARVIS_OAUTH_CLIENT_ID=CLIENT_ID_BURAYA"
```
Expected: `Service URL: https://jarvis-brain-....run.app`. Not: `--allow-unauthenticated` bilinçlidir — kimlik uygulama katmanında (Task 7 auth). Cloud Run servis hesabına Firestore ve Secret erişimi varsayılan `roles/editor` ile gelir; ilerde daraltılır (bilinçli borç, North Star §5).

- [ ] **Step 3 (HITL — Kadir): OAuth origin ekle**

Konsol → Credentials → OAuth client → Authorized JavaScript origins'e Cloud Run URL'i eklenir (yayılması birkaç dakika sürebilir).

- [ ] **Step 4: Prod smoke**

Telefonun tarayıcısından servis URL'i açılır: Google girişi → mesajlaşma → hatırlama (Task 8 Step 5'teki senaryo) → Firestore'da `audit_log` büyüyor mu kontrol. PWA olarak ana ekrana ekle ("Add to Home Screen").

- [ ] **Step 5: Commit**

```bash
git add brain/Dockerfile brain/.dockerignore
git commit -m "feat: containerize and deploy brain to Cloud Run"
```

---

### Task 10: Semantik hafıza araması (embedding + Firestore vektör)

**Files:**
- Modify: `brain/app/memory.py` (`remember_fact`, `add_lesson`, `search_memory`)
- Test: `brain/tests/test_memory.py` (ek testler)

**Interfaces:**
- Consumes: `google-genai` client (`GOOGLE_API_KEY` env'den), Firestore `find_nearest`.
- Produces: `Memory.__init__(db, embed_fn=None)` — `embed_fn: Callable[[str], list[float]] | None`; None ise Task 5'teki alt-dizgi araması (fallback) çalışmaya devam eder. `memory.make_embed_fn() -> Callable` (gerçek embedding: `gemini-embedding-001`).

- [ ] **Step 1: Failing test yaz** (test_memory.py'ye ekle)

```python
def test_search_uses_embed_fn_when_available():
    calls = []

    def fake_embed(text):
        calls.append(text)
        return [1.0, 0.0] if "kahve" in text else [0.0, 1.0]

    db = FakeDB()
    m = Memory(db, embed_fn=fake_embed)
    m.remember_fact("kahveyi X'ten söyler")
    m.remember_fact("salı akşamı arama")
    hits = m.search_memory("kahve nereden")
    assert calls  # embedding gerçekten çağrıldı
    assert "kahve" in hits[0]["text"]
```

- [ ] **Step 2: Çalıştır, FAIL gör** — `TypeError: Memory.__init__() got an unexpected keyword argument 'embed_fn'`

- [ ] **Step 3: memory.py'yi genişlet**

`Memory.__init__(self, db, embed_fn=None)`; `remember_fact`/`add_lesson` kayda `"embedding": self.embed_fn(text)` alanı ekler (embed_fn varsa; gerçek Firestore'da `Vector(...)` ile sarılır). `search_memory`: embed_fn varsa sorgu vektörüyle kosinüs benzerliği — FakeDB yolunda saf Python kosinüs, gerçek Firestore'da `collection.find_nearest(vector_field="embedding", query_vector=Vector(qv), distance_measure=DistanceMeasure.COSINE, limit=top_k)`. Gerçek embedding fabrikası:

```python
def make_embed_fn():
    from google import genai

    client = genai.Client()  # GOOGLE_API_KEY env'den

    def embed(text: str) -> list[float]:
        res = client.models.embed_content(model="gemini-embedding-001", contents=text)
        return list(res.embeddings[0].values)

    return embed
```
`main.py._init()` içinde `Memory(db, embed_fn=make_embed_fn())` kullanılır. **Doğrulama şartı:** `find_nearest` imzası ve `embed_content` dönüş şekli, implementasyon anında google-cloud-firestore / google-genai güncel dokümanından teyit edilir (fast-moving API kuralı); uyuşmazlıkta test-önce düzeltilir.

- [ ] **Step 4: Vektör indeksleri oluştur**

```bash
gcloud firestore indexes composite create --project your-gcp-project \
  --collection-group=facts --query-scope=COLLECTION \
  --field-config field-path=embedding,vector-config='{"dimension":3072,"flat":{}}'
gcloud firestore indexes composite create --project your-gcp-project \
  --collection-group=lessons --query-scope=COLLECTION \
  --field-config field-path=embedding,vector-config='{"dimension":3072,"flat":{}}'
```
Not: `dimension` değeri `gemini-embedding-001`'in gerçek çıktı boyutuyla eşleşmeli — Step 3 doğrulamasında teyit et (gerekirse `output_dimensionality` ile 768'e sabitle ve indeksi 768 aç; maliyet/hız için önerilen budur).

- [ ] **Step 5: Testler + redeploy + prod smoke**

```bash
.venv/bin/pytest -q          # tüm suite yeşil
gcloud run deploy jarvis-brain --source . --region europe-west1 --project your-gcp-project
```
Prod'da: "geçen sefer kahve hakkında ne demiştim?" → anlamsal eşleşmeyle doğru cevap.

- [ ] **Step 6: Commit**

```bash
git add brain/app/memory.py brain/tests/test_memory.py
git commit -m "feat: semantic memory search via embeddings and Firestore vector KNN"
```

---

## "Bitti" Ölçütü Eşlemesi (North Star §12, Katman 1)

| Ölçüt | Kanıt |
|---|---|
| Kendi uygulamamdan yazışıyorum | Task 9 Step 4: telefonda PWA'dan sohbet |
| Beni hatırlıyor ve tanımaya başlıyor | Task 8 Step 5 + Task 10 Step 5: oturumlar arası profil/fact hatırlama |
| Her eylem loglanıyor | Task 8/9: her araç çağrısı `audit_log`'da (zone + decision ile) |

## Kapsam Dışı (bilinçli — North Star'a sadakat)

Ses, telefon, Pi/connector, tarayıcı otomasyonu, olay katmanı, uzman ajanlar (Sekreter vb.), onay kartları UI'ı (kırmızı bölge Katman 1'de yalnızca engeller ve mesajla bildirir), haftalık retro. Bunlar Katman 2-5'in işi; bu plana eklenmez.
