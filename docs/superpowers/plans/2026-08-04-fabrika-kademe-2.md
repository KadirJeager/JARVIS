# Fabrika Kademe 2 — Onaylı Fabrika Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `propose_agent` → onay kartı → `agent_registry` kalıcı şablonu → `factory.spawn` kayıt defteri şablonlarını da tanır (North Star §8.5 Kademe 2).

**Architecture:** Araç kazanım merdiveninin (`propose_tool → _execute_tool_grant → tool_registry.grant`) birebir ajan-düzlemi izdüşümü. Yeni modül `brain/app/agent_registry.py`; `approvals.py`'ye yeni tür; `tools.py`'ye öneri aracı + yürütücü; `factory.py`'ye kayıt-defteri şablon çözümü. Spec: `docs/superpowers/specs/2026-08-04-fabrika-kademe-2-design.md`.

**Tech Stack:** Python 3.12, FastAPI/ADK mevcut yapı, Firestore (FakeDB ile test), pytest.

## Global Constraints

- **Hiçbir yeni fonksiyon exception FIRLATMAZ; her ret Türkçe gözlem metnidir** (İlke 4). Loglar `logging.info/warning/exception`.
- **Kayıt defterine yazan TEK yol onay yürütücüsüdür.** `propose_agent` ve başka hiçbir araç `agent_registry.grant` çağıramaz.
- **Doğrulama üç kez koşar:** öneri anında, grant anında (yürütücü), spawn anında (doküman fail-closed). Üçü de `agent_registry.validate_definition` tek kaynağından.
- `approvals._normalize_args` her `tool_args` değerini **500 karakterde keser** → işlevsel alan tavanları: `INSTRUCTION_MAX=500`, `PURPOSE_MAX=200`, `MAX_TOOLS=8`.
- Tavan sınırları: `MAX_STEPS_CEILING=32`, `TTL_CEILING=120`; varsayılanlar `DEFAULT_MAX_STEPS=24`, `DEFAULT_TTL_SECONDS=90`.
- Misafir anayasası DEĞİŞMEZ: `_GUEST_RULES` builder'da eklenir, `approval_sink`/`zone_resolver` verilmez, `spawn_specialist` mutlak dışlanır, çift tavan korunur.
- Kod/commit İngilizce; docstring'ler ve kullanıcıya dönen metinler Türkçe (mevcut kod stili).
- Testler `brain/` altında `pytest` ile koşar: `cd brain && python -m pytest tests/<dosya> -q`. Commit'ler `brain/` köklü repo dizininden.
- Her commit mesajı sonu: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: `agent_registry` modülü

**Files:**
- Create: `brain/app/agent_registry.py`
- Test: `brain/tests/test_agent_registry.py`

**Interfaces:**
- Consumes: `app.config` (ZONE sabitleri), `app.policy.check_zone`, geç-import `app.factory` (TEMPLATES, SPAWN_TOOL_NAME) ve `app.tools` (ALL_TOOLS).
- Produces (sonraki görevler bunlara dayanır):
  - `COLLECTION = "agent_registry"`, `STATUS_GRANTED/STATUS_REVOKED`
  - `INSTRUCTION_MAX=500, PURPOSE_MAX=200, MAX_TOOLS=8, MAX_STEPS_CEILING=32, TTL_CEILING=120, DEFAULT_MAX_STEPS=24, DEFAULT_TTL_SECONDS=90, MENU_LIMIT=20`
  - `validate_definition(*, name, purpose, instruction, tool_names, why, evidence, max_steps, ttl_seconds, catalog_names=None, shipped_names=None, zone_of=None) -> str | None`
  - `display_zone(tool_names, zone_of=None) -> str`
  - `grant(db, *, name, purpose, instruction, tool_names, max_steps, ttl_seconds, why, evidence, approval_id, now_fn=_now) -> str`
  - `revoke(db, name, *, now_fn=_now) -> str`
  - `get(db, name) -> dict | None`
  - `list_granted(db, limit=MENU_LIMIT) -> list[dict]`

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_agent_registry.py`:

```python
"""Fabrika Kademe 2 — ajan kayıt defteri (North Star §8.5, spec 2026-08-04).

Taşıyıcı pimler:
- validate_definition kırmızı araç / spawn / TEMPLATES gölgesi / tavan aşımını reddeder
- grant atomiktir: granted'a dokunulmaz, revoked yeniden kazandırılır
- revoke damgalar, SİLMEZ
"""
import pytest

from app import agent_registry, config
from tests.fakes import FakeDB

CATALOG = {"search_memory", "get_user_profile", "list_reminders", "cancel_reminder",
           "spawn_specialist"}
SHIPPED = {"arastirmaci", "arsivci", "nobetci"}
ZONES = {"search_memory": config.ZONE_GREEN, "get_user_profile": config.ZONE_GREEN,
         "list_reminders": config.ZONE_GREEN, "cancel_reminder": config.ZONE_RED,
         "spawn_specialist": config.ZONE_YELLOW}


def _validate(**over):
    base = dict(name="ozel_ajan", purpose="Test amacı.", instruction="Talimat.",
                tool_names=["search_memory"], why="Gerekçe.", evidence="3 kez tekrarlandı.",
                max_steps=24, ttl_seconds=90,
                catalog_names=CATALOG, shipped_names=SHIPPED, zone_of=ZONES.get)
    base.update(over)
    return agent_registry.validate_definition(**base)


def _grant(db, **over):
    base = dict(name="ozel_ajan", purpose="Test amacı.", instruction="Talimat.",
                tool_names=["search_memory"], max_steps=24, ttl_seconds=90,
                why="Gerekçe.", evidence="3 kez.", approval_id="ap1")
    base.update(over)
    return agent_registry.grant(db, **base)


def test_a_valid_definition_passes():
    assert _validate() is None


@pytest.mark.parametrize("over,parca", [
    (dict(name=""), "boş"),
    (dict(name="   "), "boş"),
    (dict(name="arastirmaci"), "sevkiyat"),          # TEMPLATES gölgelenemez
    (dict(tool_names=[]), "araç"),
    (dict(tool_names=["spawn_specialist"]), "recursion"),
    (dict(tool_names=["uydurma_arac"]), "uydurma_arac"),
    (dict(tool_names=["cancel_reminder"]), "kırmızı"),
    (dict(tool_names=["search_memory"] * 9), "8"),   # MAX_TOOLS aşımı
    (dict(max_steps=0), "adım"),
    (dict(max_steps=33), "adım"),
    (dict(ttl_seconds=0), "süre"),
    (dict(ttl_seconds=121), "süre"),
    (dict(purpose=""), "amaç"),
    (dict(purpose="x" * 201), "200"),
    (dict(instruction=""), "talimat"),
    (dict(instruction="x" * 501), "500"),
    (dict(why=""), "gerekçe"),
    (dict(evidence=""), "kanıt"),
])
def test_validate_rejects_bad_definitions(over, parca):
    problem = _validate(**over)
    assert problem is not None and parca.lower() in problem.lower()


def test_display_zone_is_yellow_when_any_tool_is_yellow():
    zones = {"a": config.ZONE_GREEN, "b": config.ZONE_YELLOW}
    assert agent_registry.display_zone(["a"], zone_of=zones.get) == config.ZONE_GREEN
    assert agent_registry.display_zone(["a", "b"], zone_of=zones.get) == config.ZONE_YELLOW


def test_grant_writes_a_granted_document():
    db = FakeDB()
    msg = _grant(db)
    doc = db.collection(agent_registry.COLLECTION).docs["ozel_ajan"]
    assert doc["status"] == agent_registry.STATUS_GRANTED
    assert doc["tools"] == ["search_memory"]
    assert doc["approval_id"] == "ap1"
    assert doc["revoked_at"] is None
    assert "ozel_ajan" in msg


def test_grant_validates_and_rejects_without_writing():
    db = FakeDB()
    msg = _grant(db, tool_names=["cancel_reminder"])
    assert "kırmızı" in msg.lower()
    assert db.collection(agent_registry.COLLECTION).docs == {}


def test_grant_does_not_touch_an_existing_granted_document():
    db = FakeDB()
    _grant(db)
    msg = _grant(db, instruction="Bambaşka talimat.", approval_id="ap2")
    doc = db.collection(agent_registry.COLLECTION).docs["ozel_ajan"]
    assert doc["instruction"] == "Talimat."
    assert doc["approval_id"] == "ap1"
    assert "zaten" in msg.lower()


def test_grant_regrants_a_revoked_document():
    db = FakeDB()
    _grant(db)
    agent_registry.revoke(db, "ozel_ajan")
    msg = _grant(db, approval_id="ap2")
    doc = db.collection(agent_registry.COLLECTION).docs["ozel_ajan"]
    assert doc["status"] == agent_registry.STATUS_GRANTED
    assert doc["approval_id"] == "ap2"
    assert doc["revoked_at"] is None
    assert "ozel_ajan" in msg


def test_revoke_stamps_without_deleting():
    db = FakeDB()
    _grant(db)
    msg = agent_registry.revoke(db, "ozel_ajan")
    doc = db.collection(agent_registry.COLLECTION).docs["ozel_ajan"]
    assert doc["status"] == agent_registry.STATUS_REVOKED
    assert doc["revoked_at"] is not None
    assert "ozel_ajan" in msg


def test_revoke_unknown_name_is_an_observation():
    assert "yok" in agent_registry.revoke(FakeDB(), "hayalet").lower()


def test_get_and_list_granted():
    db = FakeDB()
    _grant(db)
    _grant(db, name="ikinci_ajan", approval_id="ap2")
    agent_registry.revoke(db, "ikinci_ajan")
    assert agent_registry.get(db, "ozel_ajan")["name"] == "ozel_ajan"
    assert agent_registry.get(db, "hayalet") is None
    granted = agent_registry.list_granted(db)
    assert [d["name"] for d in granted] == ["ozel_ajan"]
```

- [ ] **Step 2: Testin FAIL ettiğini gör**

Run: `cd brain && python -m pytest tests/test_agent_registry.py -q`
Expected: `ModuleNotFoundError`/`ImportError` (agent_registry yok)

- [ ] **Step 3: Modülü yaz** — `brain/app/agent_registry.py`:

```python
"""Ajan kayıt defteri (North Star §8.5 Kademe 2): kalıcı ajanlar KOD değil VERİdir.

Kademe 2'de kalıcılaşan şey ÖRNEK değil ŞABLONDUR: onaylanan tanım bu
koleksiyona yazılır, factory.spawn onu K1 şablonlarıyla AYNI misafir
makinesinde koşturur. tool_registry ile aynı dört değişmez:

1. Kayıt yazan tek yol onay yürütücüsüdür (tools._execute_agent_grant).
2. Kayıtlı ajan misafir yüzeyini GENİŞLETEMEZ: araçları builtin katalogla
   sınırlıdır ve kırmızı araç taşıyamaz (validate_definition).
3. Uzunluk tavanları approvals._normalize_args'ın 500 karakter kesmesine
   dayanır: yürütücü şablonu KARTIN tool_args'ından kurar, işlevsel bir
   alanın kesilmesi sessiz bozulma olurdu — bu yüzden öneri anında ret.
4. Revoke damgadır, silme değil; aynı ad yeniden kazandırılabilir.

Fonksiyonlar exception FIRLATMAZ, Türkçe gözlem döner (İlke 4).
"""
import logging
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists

from . import config, policy

COLLECTION = "agent_registry"

STATUS_GRANTED = "granted"
STATUS_REVOKED = "revoked"

# Tavan üst sınırları: K1'in en büyük sevkiyat şablonu 24/90 (arastirmaci).
# Gerçek tavan değerleri henüz ölçülmedi (bilinen borç, devir notu 3 Ağu);
# üst sınır muhafazakâr tutuldu — ölçüm gelince tek sabit değişir.
MAX_STEPS_CEILING = 32
TTL_CEILING = 120
DEFAULT_MAX_STEPS = 24
DEFAULT_TTL_SECONDS = 90

# approvals._normalize_args her tool_args değerini 500 karakterde keser ve
# yürütücü şablonu kartın tool_args'ından kurar (spec §4.7). İşlevsel alanlar
# bu sınırın İÇİNDE kalmak zorunda; why/evidence belgeleyicidir, kesilebilir.
INSTRUCTION_MAX = 500
PURPOSE_MAX = 200
MAX_TOOLS = 8

MENU_LIMIT = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_names() -> set[str]:
    """Builtin araç adları — GEÇ import (tools bu modülü import eder)."""
    from . import tools

    return {getattr(fn, "__name__", "") for fn in tools.ALL_TOOLS}


def _shipped_names() -> set[str]:
    """Derleme-anı şablon adları — GEÇ import (factory döngüsü)."""
    from . import factory

    return set(factory.TEMPLATES)


def _spawn_tool_name() -> str:
    from . import factory

    return factory.SPAWN_TOOL_NAME


def validate_definition(*, name, purpose, instruction, tool_names, why, evidence,
                        max_steps, ttl_seconds, catalog_names=None,
                        shipped_names=None, zone_of=None) -> str | None:
    """Tek doğrulama kaynağı: öneri anı, grant anı ve spawn anı AYNI kuralları
    koşar (spec §4). Sorun varsa Türkçe gözlem, yoksa None.

    catalog_names/shipped_names/zone_of enjeksiyonu YALNIZ test içindir;
    üretimde None geçilir ve geç-import varsayılanları kullanılır."""
    if not isinstance(name, str) or not name.strip():
        return "Ajan adı boş olamaz."
    name = name.strip()
    shipped = _shipped_names() if shipped_names is None else shipped_names
    if name in shipped:
        return (f"'{name}' sevkiyat şablonlarından birinin adı; derleme-anı "
                "şablon gölgelenemez, başka bir ad seç.")
    if not isinstance(purpose, str) or not purpose.strip():
        return "Amaç (purpose) boş olamaz."
    if len(purpose) > PURPOSE_MAX:
        return f"Amaç en fazla {PURPOSE_MAX} karakter olabilir ({len(purpose)} verildi)."
    if not isinstance(instruction, str) or not instruction.strip():
        return "Talimat (instruction) boş olamaz."
    if len(instruction) > INSTRUCTION_MAX:
        return (f"Talimat en fazla {INSTRUCTION_MAX} karakter olabilir "
                f"({len(instruction)} verildi): onay kartının taşıyabildiği sınır bu.")
    if not isinstance(why, str) or not why.strip():
        return "Gerekçe (why) boş olamaz."
    if not isinstance(evidence, str) or not evidence.strip():
        return "Kanıt (evidence) boş olamaz: hangi tekrar eden iş, kaç kez görüldü?"
    if not tool_names:
        return "Araç listesi boş olamaz."
    if len(tool_names) > MAX_TOOLS:
        return f"En fazla {MAX_TOOLS} araç istenebilir ({len(tool_names)} verildi)."
    catalog = _catalog_names() if catalog_names is None else catalog_names
    resolve_zone = (lambda n: policy.check_zone(n)) if zone_of is None else zone_of
    spawn_name = _spawn_tool_name()   # tek gerçek factory.SPAWN_TOOL_NAME'dir
    for tool_name in tool_names:
        if tool_name == spawn_name:
            return ("Kayıtlı ajan ajan üretemez (recursion yasağı, §8.5 değişmez 3): "
                    f"'{tool_name}' araç listesine giremez.")
        if tool_name not in catalog:
            return (f"'{tool_name}' araç kataloğunda yok; kayıtlı ajan yalnız mevcut "
                    "builtin araçlardan seçebilir.")
        zone = resolve_zone(tool_name)
        if zone not in (config.ZONE_GREEN, config.ZONE_YELLOW):
            return (f"'{tool_name}' kırmızı bölgede; kayıtlı ajan kırmızı araç "
                    "taşıyamaz (§8.5 değişmez 1).")
    if not isinstance(max_steps, int) or not 1 <= max_steps <= MAX_STEPS_CEILING:
        return f"Adım tavanı 1..{MAX_STEPS_CEILING} aralığında olmalı ({max_steps!r} verildi)."
    if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= TTL_CEILING:
        return f"Süre tavanı 1..{TTL_CEILING} sn aralığında olmalı ({ttl_seconds!r} verildi)."
    return None


def display_zone(tool_names, zone_of=None) -> str:
    """Kart üzerindeki BİLGİ amaçlı bölge: küme sarı içeriyorsa sarı, yoksa yeşil.
    Yetki kararı DEĞİLDİR — yetki her araç çağrısında policy.check_zone'dadır."""
    resolve_zone = (lambda n: policy.check_zone(n)) if zone_of is None else zone_of
    zones = {resolve_zone(n) for n in tool_names}
    return config.ZONE_YELLOW if config.ZONE_YELLOW in zones else config.ZONE_GREEN


def grant(db, *, name, purpose, instruction, tool_names, max_steps, ttl_seconds,
          why, evidence, approval_id, now_fn=_now) -> str:
    """Kalıcı ajanı kayıt defterine yazar. ÜRETİMDE TEK ÇAĞIRAN onay
    yürütücüsüdür (tools._execute_agent_grant) — bir araç gövdesinden çağrılması
    insan yetkilendirmesini baypas etmek demektir (tool_registry.grant sözleşmesi).

    Çakışma davranışı tool_registry.grant ile AYNI: atomik create();
    granted -> DOKUNULMAZ; revoked -> yeniden kazandırılır (merge=True,
    revoked_at temizlenir)."""
    problem = validate_definition(
        name=name, purpose=purpose, instruction=instruction, tool_names=tool_names,
        why=why, evidence=evidence, max_steps=max_steps, ttl_seconds=ttl_seconds)
    if problem:
        logging.warning("agent_registry: grant reddedildi name=%r -- %s", name, problem)
        return problem

    name = name.strip()
    doc = {
        "name": name,
        "purpose": purpose,
        "instruction": instruction,
        "tools": list(tool_names),
        "max_steps": max_steps,
        "ttl_seconds": ttl_seconds,
        "status": STATUS_GRANTED,
        "why": why,
        "evidence": evidence,
        "approval_id": approval_id,
        "granted_at": now_fn(),
        "revoked_at": None,
    }
    ref = db.collection(COLLECTION).document(name)
    try:
        ref.create(doc)
    except AlreadyExists:
        current = ref.get().to_dict() or {}
        if current.get("status") == STATUS_GRANTED:
            logging.info("agent_registry: '%s' zaten kayıtlı -- üzerine yazılmadı", name)
            return f"'{name}' zaten ajan kayıt defterinde kayıtlı; kayıt değiştirilmedi."
        ref.set(doc, merge=True)
        logging.info("agent_registry: '%s' yeniden kazandırıldı (önceki durum: %s)",
                     name, current.get("status"))

    logging.info("agent_registry: grant name=%s araclar=%s tavanlar=%d/%ds approval=%s",
                 name, ",".join(tool_names), max_steps, ttl_seconds, approval_id)
    return (f"Kalıcı ajan '{name}' kayıt defterine yazıldı; artık spawn_specialist "
            "ile bu adla çağrılabilir.")


def revoke(db, name: str, *, now_fn=_now) -> str:
    """Kalıcı ajanı geri alır: kayıt SİLİNMEZ, revoked damgalanır (audit izi
    okunur kalır); aynı ad yeniden önerilebilir (grant'ın revoked dalı)."""
    ref = db.collection(COLLECTION).document(name)
    snap = ref.get()
    if not snap.exists:
        return f"'{name}' ajan kayıt defterinde yok."
    ref.set({"status": STATUS_REVOKED, "revoked_at": now_fn()}, merge=True)
    logging.info("agent_registry: revoke name=%s", name)
    return f"Kalıcı ajan '{name}' geri alındı; spawn artık bu adı tanımayacak."


def get(db, name: str) -> dict | None:
    """Tek kayıt, durumu ne olursa olsun; yoksa None."""
    snap = db.collection(COLLECTION).document(name).get()
    return snap.to_dict() if snap.exists else None


def list_granted(db, limit: int = MENU_LIMIT) -> list[dict]:
    """Menü için granted kayıtlar, ada göre sıralı, limitli.

    Tek-alan status filtresi: composite index GEREKMEZ (conversations 502
    vakasının sınıfına girmez — sıralama Python tarafında)."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    snaps = (db.collection(COLLECTION)
             .where(filter=FieldFilter("status", "==", STATUS_GRANTED))
             .stream())
    docs = [s.to_dict() for s in snaps]
    docs.sort(key=lambda d: d.get("name") or "")
    return docs[:limit]
```

- [ ] **Step 4: Testlerin geçtiğini gör**

Run: `cd brain && python -m pytest tests/test_agent_registry.py -q`
Expected: hepsi PASS

- [ ] **Step 5: Mevcut süitin kırılmadığını doğrula**

Run: `cd brain && python -m pytest -q`
Expected: tümü yeşil (721+ test)

- [ ] **Step 6: Commit**

```bash
git add brain/app/agent_registry.py brain/tests/test_agent_registry.py
git commit -m "feat(factory): agent registry module for tier-2 permanent agents

Single validation source (validate_definition) used at propose, grant and
spawn time. Grant mirrors tool_registry: atomic create, granted untouched,
revoked re-grantable. Functional field ceilings pinned to the 500-char
tool_args normalisation.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `approvals` — `agent_grant` türü

**Files:**
- Modify: `brain/app/approvals.py:50-87` (tür sabitleri bölgesi)
- Test: `brain/tests/test_approvals.py` (dosyanın sonuna ekle)

**Interfaces:**
- Produces: `approvals.KIND_AGENT_GRANT = "agent_grant"`, `approvals.EXECUTOR_AGENT_GRANT = "kind:agent_grant"`; `_executor_key` bu tür için isim-uzaylı anahtarı döner; `EXECUTABLE_KINDS` üç türlü olur.

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_approvals.py` sonuna:

```python
# ---------------------------------------------------------------------------
# Kademe 2: kind=agent_grant (Fabrika K2 spec §5)
# ---------------------------------------------------------------------------


def test_agent_grant_is_an_executable_kind():
    assert approvals.KIND_AGENT_GRANT in approvals.EXECUTABLE_KINDS


def test_executor_key_resolves_agent_grant_to_its_namespaced_key():
    key = approvals._executor_key({"kind": approvals.KIND_AGENT_GRANT,
                                   "tool_name": "ozel_ajan"})
    assert key == approvals.EXECUTOR_AGENT_GRANT == "kind:agent_grant"


def test_a_tool_call_cannot_borrow_the_agent_grant_executor():
    """kind: ön eki isim-uzayı sınırıdır — kendini 'kind:agent_grant' diye
    adlandıran bir tool_call yürütücüyü ödünç alamaz (tool_grant pini ile aynı)."""
    key = approvals._executor_key({"kind": approvals.KIND_TOOL_CALL,
                                   "tool_name": "kind:agent_grant"})
    assert key is None
```

- [ ] **Step 2: FAIL gör**

Run: `cd brain && python -m pytest tests/test_approvals.py -q`
Expected: `AttributeError: ... KIND_AGENT_GRANT`

- [ ] **Step 3: Uygula** — `brain/app/approvals.py`'de üç düzenleme:

`KIND_TOOL_GRANT` tanımının hemen altına:

```python
# Fabrika Kademe 2 (§8.5): "öneri onay merkezine düşer; Kadir onaylarsa kayıt
# defterine kalıcı ajan olarak yazılır". Yürütülmesi = agent_registry.grant().
KIND_AGENT_GRANT = "agent_grant"
```

`EXECUTOR_TOOL_GRANT` satırının altına:

```python
EXECUTOR_AGENT_GRANT = "kind:agent_grant"
```

`EXECUTABLE_KINDS` satırını değiştir (yorumundaki "ileride gelecek agent_spec" cümlesi artık dolduğu için yorumu da güncelle):

```python
# Onaylandığında bir yürütücü koşan türler. Bu kümede OLMAYAN bir tür onaylanır
# ama yan etkisi yoktur — Y3'ün "yürütülebilir tek tür tool_call" davranışının
# genelleştirilmiş hâli. (§8.5'in agent_spec öngörüsü Kademe 2 ile dolduruldu.)
EXECUTABLE_KINDS = (KIND_TOOL_CALL, KIND_TOOL_GRANT, KIND_AGENT_GRANT)
```

`_executor_key` içinde `if kind == KIND_TOOL_GRANT:` bloğunun altına:

```python
    if kind == KIND_AGENT_GRANT:
        return EXECUTOR_AGENT_GRANT
```

- [ ] **Step 4: PASS gör + tam süit**

Run: `cd brain && python -m pytest tests/test_approvals.py -q && python -m pytest -q`
Expected: tümü yeşil

- [ ] **Step 5: Commit**

```bash
git add brain/app/approvals.py brain/tests/test_approvals.py
git commit -m "feat(approvals): agent_grant approval kind with namespaced executor key

Fills the agent_spec slot the module comment reserved. Same kind: namespace
guard as tool_grant: a tool_call cannot borrow the registry-writing executor.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `propose_agent` aracı + `_execute_agent_grant` yürütücüsü

**Files:**
- Modify: `brain/app/tools.py` (araç kazanım merdiveni bölümünün sonu, `propose_tool`/`_execute_tool_grant`/`register_executor` bloğundan sonra; ayrıca dosya sonundaki `ALL_TOOLS` listesi)
- Modify: `brain/app/config.py` (`TOOL_ZONES` sözlüğü — `"propose_tool"` girdisinin yanına)
- Test: `brain/tests/test_agent_proposals.py` (yeni dosya)

**Interfaces:**
- Consumes: Task 1'in `agent_registry.validate_definition/grant/get/display_zone` + sabitleri; Task 2'nin `KIND_AGENT_GRANT`/`EXECUTOR_AGENT_GRANT`'ı; mevcut `approvals.request/find_pending_duplicate/register_executor`.
- Produces: `tools.propose_agent(name, purpose, instruction, tools, why, evidence, tool_context, max_steps=24, ttl_seconds=90) -> str` (ALL_TOOLS'ta, yeşil bölgede); `tools._execute_agent_grant(tool_args, user_id) -> str` (kayıt defterine yazan TEK yol); `tools.AGENT_PROPOSAL_QUEUED` sabiti.

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_agent_proposals.py`:

```python
"""Fabrika Kademe 2 — propose_agent (spec §4-§5): Jarvis ÖNERİR, Kadir VERİR.

Taşıyıcı pimler (test_tool_proposals ile aynı sınıf):
- test_propose_agent_writes_nothing_to_the_registry: öneri yalnız kart kurar.
- test_rejected_agent_grant_writes_nothing: onay olmadan kayıt olmaz.
- decide(approved) yolu ÜRETİMİN GERÇEK DİZİSİDİR (tek olaylı kurulum değil).
"""
from types import SimpleNamespace

from app import agent_registry, approvals, config, tools
from app.memory import Memory
from tests.fakes import FakeDB, wired_into_all_tools

USER = "kadir@example.com"
SESSION_ID = "s1"


def _ctx(user_id=USER, session_id=SESSION_ID):
    return SimpleNamespace(session=SimpleNamespace(user_id=user_id, id=session_id))


def _db():
    db = FakeDB()
    tools.init(Memory(db))
    return db


def _propose(*, name="ozel_arastirmaci", purpose="Nöbet raporlarını derler.",
             instruction="Sen nöbet raporu derleyen bir uzmansın.",
             tool_csv="check_my_vitals,list_reminders",
             why="Kadir her sabah aynı raporu istiyor.",
             evidence="Son hafta 5 kez nobetci şablonu aynı hedefle koştu.",
             ctx=None, max_steps=24, ttl_seconds=90):
    return tools.propose_agent(name, purpose, instruction, tool_csv, why, evidence,
                               ctx or _ctx(), max_steps=max_steps, ttl_seconds=ttl_seconds)


def _approvals(db):
    return list(db.collection(approvals.COLLECTION).docs.items())


def _registry(db):
    return db.collection(agent_registry.COLLECTION).docs


def test_propose_agent_writes_nothing_to_the_registry():
    """TAŞIYICI PİM (spec §5). Öneri bir onay kurar; ajan kayıt defteri BOŞ kalır."""
    db = _db()
    msg = _propose()
    assert _registry(db) == {}
    (aid, doc), = _approvals(db)
    assert doc["kind"] == approvals.KIND_AGENT_GRANT
    assert doc["status"] == approvals.STATUS_PENDING
    assert doc["tool_name"] == "ozel_arastirmaci"
    assert doc["tool_args"]["approval_id"] == aid
    assert "ozel_arastirmaci" in msg


def test_the_card_shows_the_full_instruction_and_evidence():
    """Kart onayı fiilen kod incelemesidir (spec §4): talimat ve kanıt detayda."""
    db = _db()
    _propose()
    (_, doc), = _approvals(db)
    assert "Sen nöbet raporu derleyen bir uzmansın." in doc["detail"]
    assert "Son hafta 5 kez" in doc["detail"]
    assert "check_my_vitals" in doc["detail"]


def test_propose_agent_rejects_a_red_tool_without_creating_a_card():
    db = _db()
    msg = _propose(tool_csv="check_my_vitals,cancel_reminder")
    assert "kırmızı" in msg.lower()
    assert _approvals(db) == [] and _registry(db) == {}


def test_propose_agent_rejects_a_shipped_template_name():
    db = _db()
    msg = _propose(name="arastirmaci")
    assert "sevkiyat" in msg.lower() or "gölge" in msg.lower()
    assert _approvals(db) == []


def test_propose_agent_rejects_an_unknown_tool():
    db = _db()
    msg = _propose(tool_csv="uydurma_arac")
    assert "uydurma_arac" in msg
    assert _approvals(db) == []


def test_propose_agent_rejects_the_spawn_tool():
    db = _db()
    msg = _propose(tool_csv="spawn_specialist")
    assert "recursion" in msg.lower() or "üretemez" in msg.lower()
    assert _approvals(db) == []


def test_propose_agent_rejects_cap_overflow():
    db = _db()
    assert "adım" in _propose(max_steps=33).lower()
    assert "süre" in _propose(ttl_seconds=121).lower()
    assert _approvals(db) == []


def test_propose_agent_refuses_an_already_granted_name():
    db = _db()
    _propose()
    (aid, _), = _approvals(db)
    approvals.decide(db, aid, USER, "approved", executors=approvals.EXECUTORS)
    msg = _propose()
    assert "zaten" in msg.lower()
    assert len(_approvals(db)) == 1          # ikinci kart YOK


def test_propose_agent_does_not_create_a_second_card_for_a_pending_name():
    db = _db()
    _propose()
    msg = _propose()
    assert len(_approvals(db)) == 1
    assert "bekleyen" in msg.lower() or "ikinci" in msg.lower()


def test_approved_agent_grant_writes_the_registry_document():
    """ÜRETİMİN GERÇEK DİZİSİ: request -> decide(approved) -> yürütücü -> grant."""
    db = _db()
    _propose()
    (aid, _), = _approvals(db)
    result = approvals.decide(db, aid, USER, "approved", executors=approvals.EXECUTORS)
    assert result["status"] == approvals.STATUS_APPROVED
    doc = _registry(db)["ozel_arastirmaci"]
    assert doc["status"] == agent_registry.STATUS_GRANTED
    assert doc["tools"] == ["check_my_vitals", "list_reminders"]
    assert doc["max_steps"] == 24 and doc["ttl_seconds"] == 90
    assert doc["approval_id"] == aid
    assert "yazıldı" in (result["outcome"] or "")


def test_rejected_agent_grant_writes_nothing():
    db = _db()
    _propose()
    (aid, _), = _approvals(db)
    approvals.decide(db, aid, USER, "rejected", executors=approvals.EXECUTORS)
    assert _registry(db) == {}


def test_executor_revalidates_corrupted_args():
    """Öneriyle karar arasında dünya değişebilir (spec §9.2): bozuk tool_args
    kayıt YAZMAZ, Türkçe gözlem döner."""
    msg = tools._execute_agent_grant(
        {"name": "x", "purpose": "p", "instruction": "t",
         "tools": "cancel_reminder", "max_steps": "24", "ttl_seconds": "90",
         "why": "w", "evidence": "e", "approval_id": "a"}, USER)
    assert "kırmızı" in msg.lower()


def test_executor_survives_non_numeric_caps():
    msg = tools._execute_agent_grant(
        {"name": "x", "purpose": "p", "instruction": "t",
         "tools": "check_my_vitals", "max_steps": "bozuk", "ttl_seconds": "90",
         "why": "w", "evidence": "e", "approval_id": "a"}, USER)
    assert "tavan" in msg.lower() or "adım" in msg.lower()


def test_propose_agent_is_green_and_registered_as_a_tool():
    assert config.TOOL_ZONES["propose_agent"] == config.ZONE_GREEN
    assert wired_into_all_tools(tools.propose_agent)


def test_backend_failure_is_an_observation():
    """İlke 4: araç fırlatmaz, gözlem döner (test_tool_proposals ikizi)."""

    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    tools.init(Memory(BoomDB()))
    out = _propose()
    assert isinstance(out, str) and ("oluşturulam" in out.lower() or "kapalı" in out.lower())
```

- [ ] **Step 2: FAIL gör**

Run: `cd brain && python -m pytest tests/test_agent_proposals.py -q`
Expected: `AttributeError: module 'app.tools' has no attribute 'propose_agent'`

- [ ] **Step 3: Uygula** — `brain/app/tools.py`, `register_executor(approvals.EXECUTOR_TOOL_GRANT, ...)` satırından SONRA yeni bölüm:

```python
# -- Ajan fabrikası Kademe 2 (North Star §8.5, spec 2026-08-04) --------------
#
# Araç merdiveninin ajan-düzlemi izdüşümü: propose_agent yalnızca onay kurar,
# _execute_agent_grant yalnızca onay sonrası çalışır. Ajan kayıt defterine
# yazan TEK yer aşağıdaki yürütücüdür.

AGENT_PROPOSAL_QUEUED = (
    "ÖNERİ ONAYA GÖNDERİLDİ: kalıcı ajan '{name}' için Kadir'e onay kartı "
    "oluşturuldu. Kadir'e öneriyi ve gerekçeni söyle, sonra BEKLE — aynı ajan "
    "için ikinci bir kart oluşturma, kararı kart üzerinden verir."
)


def _agent_tool_names(tool_csv: str) -> list[str]:
    """Virgüllü araç listesini ada çevirir (propose_tool.scopes ile aynı biçim:
    onay tool_args'ı düz string taşır, yapıya kayıt anında çevrilir)."""
    return [t.strip() for t in (tool_csv or "").split(",") if t.strip()]


def propose_agent(name: str, purpose: str, instruction: str, tools: str, why: str,
                  evidence: str, tool_context, max_steps: int = 24,
                  ttl_seconds: int = 90) -> str:
    """Tekrar eden bir iş için KALICI bir uzman ajan tanımını Kadir'in onayına
    önerir. Kendi başına hiçbir şey kurmaz: yalnızca onay kartı oluşturur,
    kararı Kadir verir. `name` yeni ajanın adı (sevkiyat şablonlarından farklı
    olmalı); `purpose` menüde görünecek tek cümlelik Türkçe amaç; `instruction`
    ajanın sistem talimatı (en fazla 500 karakter); `tools` virgülle ayrılmış
    builtin araç adları (en fazla 8, kırmızı bölge araçları istenemez,
    spawn_specialist istenemez); `why` Kadir'in kartta okuyacağı gerekçe;
    `evidence` kanıt — hangi tekrar eden iş, kaç kez görüldü (zorunlu).
    Onaydan sonra ajan spawn_specialist ile bu adla hemen çağrılabilir.
    Bir ajanı önerdikten sonra BEKLE, kendin kurmaya çalışma."""
    try:
        tool_names = _agent_tool_names(tools)
        problem = agent_registry.validate_definition(
            name=name, purpose=purpose, instruction=instruction,
            tool_names=tool_names, why=why, evidence=evidence,
            max_steps=max_steps, ttl_seconds=ttl_seconds)
        if problem:
            return problem
        name = name.strip()

        db = _memory.db
        existing = agent_registry.get(db, name)
        if existing and existing.get("status") == agent_registry.STATUS_GRANTED:
            return (f"'{name}' zaten ajan kayıt defterinde kayıtlı; "
                    "yeni öneri gerekmiyor.")

        session = tool_context.session
        user_id, session_id = session.user_id, session.id
        # find_pending_duplicate, list_pending DEĞİL -- propose_tool'daki gerekçe:
        # MAX_PENDING kesmesi mükerrer kontrolünde kör nokta bırakır.
        if approvals.find_pending_duplicate(db, user_id, name, {},
                                            kind=approvals.KIND_AGENT_GRANT):
            return (f"'{name}' için zaten Kadir'in kararını bekleyen bir öneri var; "
                    "ikinci kart oluşturmadım.")

        zone = agent_registry.display_zone(tool_names)
        zone_lines = "\n".join(
            f"  - {t} ({policy.check_zone(t)})" for t in tool_names)
        approval_id = uuid.uuid4().hex
        approvals.request(
            db,
            user_id=user_id,
            kind=approvals.KIND_AGENT_GRANT,
            title=f"Kalıcı ajan '{name}' kurulsun mu?",
            detail=(f"Jarvis kalıcı bir uzman ajan öneriyor: {name} ({zone} bölge).\n"
                    f"Amaç: {purpose}\n"
                    f"Talimat (modele birebir gidecek metin):\n{instruction}\n"
                    f"Araçlar:\n{zone_lines}\n"
                    f"Tavanlar: {max_steps} adım / {ttl_seconds} sn\n"
                    f"Gerekçe: {why}\n"
                    f"Kanıt: {evidence}\n"
                    "Onaylarsan ajan kayıt defterine yazılır ve spawn_specialist "
                    "ile hemen çağrılabilir."),
            tool_name=name,
            tool_args={"name": name, "purpose": purpose, "instruction": instruction,
                       "tools": ",".join(tool_names), "max_steps": str(max_steps),
                       "ttl_seconds": str(ttl_seconds), "why": why,
                       "evidence": evidence, "approval_id": approval_id},
            zone=zone,
            session_id=session_id,
            doc_id=approval_id,
        )
        logging.info("propose_agent: öneri onaya düştü name=%s araclar=%s id=%s",
                     name, ",".join(tool_names), approval_id)
        return AGENT_PROPOSAL_QUEUED.format(name=name)
    except Exception:
        logging.exception("propose_agent: öneri oluşturulamadı name=%r", name)
        return "Bu öneri şu an oluşturulamıyor; kalıcı ajan yolu geçici olarak kapalı."


def _execute_agent_grant(tool_args: dict, user_id: str) -> str:
    """`kind=agent_grant` onaylarının yürütücüsü (spec §5). AJAN KAYIT
    DEFTERİNE YAZAN TEK YERDİR — `approvals.decide(..., "approved")` dışında
    hiçbir yol buraya varmaz; grant içindeki validate_definition öneriyle
    karar arasında dünyanın değişmiş olabileceğine karşı yeniden koşar
    (_execute_tool_grant sözleşmesinin aynısı)."""
    args = tool_args or {}
    try:
        max_steps = int(str(args.get("max_steps", "")))
        ttl_seconds = int(str(args.get("ttl_seconds", "")))
    except ValueError:
        logging.warning("agent_grant: sayısal olmayan tavan tool_args=%r", args)
        return "Ajan tavanları sayı değil; kayıt yazılmadı (öneri bozulmuş olabilir)."
    return agent_registry.grant(
        _memory.db,
        name=str(args.get("name", "")),
        purpose=str(args.get("purpose", "")),
        instruction=str(args.get("instruction", "")),
        tool_names=_agent_tool_names(str(args.get("tools", ""))),
        max_steps=max_steps,
        ttl_seconds=ttl_seconds,
        why=str(args.get("why", "")),
        evidence=str(args.get("evidence", "")),
        approval_id=str(args.get("approval_id", "")),
    )


approvals.register_executor(approvals.EXECUTOR_AGENT_GRANT, _execute_agent_grant)
```

Üç kablolama düzenlemesi:

1. `tools.py` başındaki import satırına `agent_registry` ve `policy` ekle:

```python
from . import (agent_registry, approvals, consult, factory, policy, reminders,
               repo_watch, speaker_history, speaker_store, tool_registry, vitals,
               voice_trust)
```

2. `ALL_TOOLS` listesine `propose_tool`'un yanına `propose_agent` ekle:

```python
    set_reminder, list_reminders, cancel_reminder, propose_tool, propose_agent,
    spawn_specialist)]
```

3. `config.py` `TOOL_ZONES` sözlüğünde `"propose_tool"` girdisinin yanına:

```python
    "propose_agent": ZONE_GREEN,
```

- [ ] **Step 4: PASS gör + tam süit**

Run: `cd brain && python -m pytest tests/test_agent_proposals.py -q && python -m pytest -q`
Expected: tümü yeşil. (Dikkat: `policy` importu döngü kurarsa — `policy` `tools`'u import etmiyor, güvenli — yine de tam süit bunu kanıtlar.)

- [ ] **Step 5: Commit**

```bash
git add brain/app/tools.py brain/app/config.py brain/tests/test_agent_proposals.py
git commit -m "feat(factory): propose_agent tool and agent_grant executor

Proposal only builds an approval card showing the full instruction text;
the executor is the single registry writer and revalidates before granting.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `factory.spawn` kayıt-defteri şablonlarını tanır

**Files:**
- Modify: `brain/app/factory.py` (`unknown_template_reply` ve `spawn`; yeni yardımcılar)
- Modify: `brain/app/tools.py:399-409` (`spawn_specialist` docstring'i)
- Test: `brain/tests/test_factory_registry.py` (yeni dosya)
- Test: `brain/tests/test_retro.py` (sonuna bir pin)

**Interfaces:**
- Consumes: Task 1'in `agent_registry.get/list_granted/validate_definition/STATUS_GRANTED`'ı; mevcut `AgentTemplate`, `_GUEST_RULES`, `resolve_tools`, `build_specialist`, `spawn`.
- Produces: `factory._registry_template(name) -> AgentTemplate | None` (üretim varsayılanı), `factory._registry_menu() -> list[dict]`; `spawn(..., registry_lookup=None, registry_menu=None)` iki yeni enjeksiyon noktası (yalnız test için); `unknown_template_reply(template_name, registered=None)`.

- [ ] **Step 1: Başarısız testleri yaz** — `brain/tests/test_factory_registry.py`:

```python
"""Fabrika Kademe 2 — kayıt defteri şablonlarıyla spawn (spec §6).

Taşıyıcı pimler:
- Kayıtlı şablonun instruction'ına _GUEST_RULES BUILDER'da eklenir (veriye güvenilmez).
- TEMPLATES kayıt defterini GÖLGELER (derleme anı her zaman kazanır).
- revoked/bozuk doküman fail-closed: spawn bilinmeyen şablon menüsüne düşer.
- Menü iki kaynağı etiketleriyle listeler.
"""
from app import agent_registry, factory
from tests.fakes import FakeDB
from tests.test_factory import FakeAudit, FakeEvent, FakeRunner, _runner_factory

USER = "owner@example.com"


def _granted_doc(**over):
    base = dict(name="ozel_arastirmaci", purpose="Özel araştırma.",
                instruction="Sen özel bir araştırmacısın.",
                tools=["search_memory"], max_steps=10, ttl_seconds=30,
                status=agent_registry.STATUS_GRANTED, why="w", evidence="e",
                approval_id="ap1", granted_at="2026-08-04T00:00:00+00:00",
                revoked_at=None)
    base.update(over)
    return base


def _lookup_from(doc):
    """Üretimdeki _registry_template'in davranış ikizi: testte Firestore yerine
    sözlükten okur ama AYNI kurulum fonksiyonunu kullanır."""
    return lambda name: factory._template_from_doc(doc) if doc and doc["name"] == name else None


async def _spawn_registry(doc, *, name=None, runner=None):
    return await factory.spawn(
        name or (doc["name"] if doc else "hayalet"), "hedef", user_id=USER,
        audit=FakeAudit(), model="fake-model", instance="i1",
        runner_factory=_runner_factory(runner or FakeRunner([FakeEvent("bitti")])),
        clock=lambda: 0.0, registry_lookup=_lookup_from(doc),
        registry_menu=lambda: [])


async def test_a_registered_agent_spawns_and_returns_a_result():
    # pytest-asyncio asyncio_mode="auto": düz async def yeterli, dekoratör yok.
    result = await _spawn_registry(_granted_doc())
    assert result["durum"] == factory.STATUS_OK
    assert result["sablon"] == "ozel_arastirmaci"
    assert result["sonuc"] == "bitti"


def test_guest_rules_are_appended_by_the_builder_not_trusted_from_data():
    template = factory._template_from_doc(_granted_doc())
    assert template.instruction.startswith("Sen özel bir araştırmacısın.")
    assert factory._GUEST_RULES.strip() in template.instruction
    assert template.tools == ("search_memory",)
    assert template.max_steps == 10 and template.ttl_seconds == 30


async def test_a_shipped_template_shadows_a_registry_agent_with_the_same_name():
    """TEMPLATES önce ve her zaman kazanır: aynı adla kayıt varsa bile spawn
    sevkiyat şablonunu koşturur (propose_agent bu adı zaten reddeder; bu pin
    elle yazılmış/bozulmuş kayda karşı savunmadır)."""
    doc = _granted_doc(name="arastirmaci", instruction="Sahte gölge.")
    # spawn'ın şablon çözümü: known.get önce -- registry_lookup'a hiç inilmez.
    # Bunu lookup'ı sayaçlayarak kanıtlıyoruz.
    calls = []

    def lookup(name):
        calls.append(name)
        return factory._template_from_doc(doc)

    result = await factory.spawn(
        "arastirmaci", "hedef", user_id=USER, audit=FakeAudit(), model="fake-model",
        instance="i1", runner_factory=_runner_factory(FakeRunner([FakeEvent("ok")])),
        clock=lambda: 0.0, registry_lookup=lookup, registry_menu=lambda: [])
    assert calls == []                      # kayıt defterine hiç bakılmadı
    assert result["sablon"] == "arastirmaci"


async def test_a_revoked_agent_is_not_spawnable():
    # Üretim ikizi: _registry_template yalnız granted dokümanı şablona çevirir;
    # revoked için lookup None döner (üretim kodundaki status kontrolü).
    result = await factory.spawn(
        "ozel_arastirmaci", "hedef", user_id=USER, audit=FakeAudit(),
        model="fake-model", instance="i1",
        runner_factory=_runner_factory(FakeRunner([FakeEvent("x")])),
        clock=lambda: 0.0,
        registry_lookup=lambda name: None,
        registry_menu=lambda: [])
    assert result["durum"] == factory.STATUS_ERROR
    assert "kullanilabilir_sablonlar" in result


def test_a_corrupted_document_is_rejected_fail_closed():
    """Elle bozulmuş doküman (kırmızı araç sokulmuş) şablona DÖNÜŞMEZ."""
    doc = _granted_doc(tools=["cancel_reminder"])
    assert factory._template_from_doc(doc) is None


def test_the_menu_lists_both_sources_with_labels():
    reply = factory.unknown_template_reply(
        "hayalet", registered=[{"name": "ozel_arastirmaci",
                                "purpose": "Özel araştırma.",
                                "tools": ["search_memory"]}])
    kaynaklar = {s.get("kaynak") for s in reply["kullanilabilir_sablonlar"]}
    assert kaynaklar == {"sevkiyat", "kayit_defteri"}
    adlar = [s["ad"] for s in reply["kullanilabilir_sablonlar"]]
    assert "ozel_arastirmaci" in adlar and "arastirmaci" in adlar
```

`brain/tests/test_retro.py` sonuna pin (dosyadaki mevcut `_factory_audit`
yardımcısını ve `retro.collect_week` sözleşmesini kullanır — `test_retro.py:185`
civarındaki desen):

```python
def test_retro_counts_a_registry_sourced_factory_actor():
    """Kademe 2 pini (§8.5 değişmez 6): kayıt-defteri kaynaklı ajan koşuları
    sevkiyat şablonlarıyla AYNI factory: aktör biçimini kullandığı için haftalık
    retro envanteri onları EK KOD OLMADAN sayar. Bu pin düşerse retro kayıtlı
    ajanlara körleşmiş demektir."""
    db = FakeDB()
    now = "2026-08-03T12:00:00+00:00"
    recent = "2026-08-01T12:00:00+00:00"
    _factory_audit(db, "factory:arastirmaci#a1", recent)          # sevkiyat
    _factory_audit(db, "factory:ozel_arastirmaci#r1", recent)     # kayıt defteri
    _factory_audit(db, "factory:ozel_arastirmaci#r2", recent)

    out = retro.collect_week(db, now_fn=lambda: now)["factory"]

    assert out["by_template"] == {"arastirmaci": 1, "ozel_arastirmaci": 2}
    assert out["instances_7d"] == 3
```

- [ ] **Step 2: FAIL gör**

Run: `cd brain && python -m pytest tests/test_factory_registry.py -q`
Expected: `AttributeError: ... _template_from_doc` (ve spawn bilinmeyen kwarg hatası)

- [ ] **Step 3: Uygula** — `brain/app/factory.py`:

`unknown_template_reply`'ı değiştir:

```python
def unknown_template_reply(template_name, registered: list[dict] | None = None) -> dict:
    """Bilinmeyen şablon → Türkçe gözlem + menü (İlke 4: FIRLATMAZ).

    Menü iki kaynaklıdır (Kademe 2): sevkiyat şablonları + kayıt defterindeki
    kalıcı ajanlar, her satır kaynağını söyler. Menü olmadan model kör kalır
    ve aynı yanlış adı tekrar dener."""
    menu = [
        {"ad": t.name, "amac": t.purpose, "araclar": list(t.tools),
         "kaynak": "sevkiyat"}
        for t in sorted(TEMPLATES.values(), key=lambda t: t.name)
    ] + [
        {"ad": d.get("name"), "amac": d.get("purpose"),
         "araclar": list(d.get("tools") or []), "kaynak": "kayit_defteri"}
        for d in (registered or [])
    ]
    return {
        "durum": STATUS_ERROR,
        "hata": (f"'{template_name}' diye bir ajan şablonu yok. "
                 "Aşağıdakilerden birini seç."),
        "kullanilabilir_sablonlar": menu,
    }
```

Yeni yardımcılar (`unknown_template_reply`'ın üstüne):

```python
def _template_from_doc(doc: dict) -> "AgentTemplate | None":
    """Kayıt defteri dokümanından şablon kurar; bozuksa None (fail-closed).

    İki bilinçli adım (spec §6):
    * Doküman ALANLARI yeniden doğrulanır — elle yazılmış/bozulmuş bir kayıt
      (kırmızı araç sokulmuş, tavan şişirilmiş) şablona dönüşemez.
    * `_GUEST_RULES` BURADA eklenir; depolanan metne güvenilmez — veri
      kanalından misafir anayasası gevşetilemez."""
    from . import agent_registry

    try:
        tool_names = list(doc.get("tools") or [])
        problem = agent_registry.validate_definition(
            name=str(doc.get("name") or ""), purpose=str(doc.get("purpose") or ""),
            instruction=str(doc.get("instruction") or ""), tool_names=tool_names,
            why=str(doc.get("why") or "-"), evidence=str(doc.get("evidence") or "-"),
            max_steps=doc.get("max_steps"), ttl_seconds=doc.get("ttl_seconds"))
        if problem:
            logging.warning("factory: kayıt defteri dokümanı reddedildi name=%r -- %s",
                            doc.get("name"), problem)
            return None
        return AgentTemplate(
            name=str(doc["name"]).strip(),
            purpose=str(doc["purpose"]),
            instruction=str(doc["instruction"]) + _GUEST_RULES,
            tools=tuple(tool_names),
            max_steps=int(doc["max_steps"]),
            ttl_seconds=int(doc["ttl_seconds"]),
        )
    except Exception:
        logging.exception("factory: kayıt defteri dokümanı şablona çevrilemedi name=%r",
                          doc.get("name"))
        return None


def _registry_template(template_name: str) -> "AgentTemplate | None":
    """Üretim kayıt-defteri okuması: spawn anında CANLI tek doküman.

    Araç tarafından bilinçli fark (spec §6): MCP toolset'leri açılışta
    bağlanır, veri şablonuysa onaydan hemen sonra kullanılabilir — "üretim
    onay anında gerçekleşir" (§8.5 Kademe 2). FIRLATMAZ: her hata None'dır
    (çağıran menüye düşer), sebep loglanır."""
    from . import agent_registry
    from . import tools as tools_mod

    try:
        doc = agent_registry.get(tools_mod._memory.db, template_name)
        if not doc or doc.get("status") != agent_registry.STATUS_GRANTED:
            return None
        return _template_from_doc(doc)
    except Exception:
        logging.exception("factory: kayıt defteri okunamadı sablon=%r", template_name)
        return None


def _registry_menu() -> list[dict]:
    """Menü için granted kayıtlar; hata boş listedir (menü süs değil ama
    yokluğu spawn'ı düşürmemeli)."""
    from . import agent_registry
    from . import tools as tools_mod

    try:
        return agent_registry.list_granted(tools_mod._memory.db)
    except Exception:
        logging.exception("factory: kayıt defteri menüsü okunamadı")
        return []
```

`spawn` imzasını ve şablon çözümünü değiştir (mevcut gövdenin İLK bloğu):

```python
async def spawn(template_name, goal: str, *, user_id: str, audit, model=None,
                instance: str | None = None, runner_factory=None,
                clock: Callable[[], float] = time.monotonic,
                templates: dict | None = None,
                registry_lookup=None, registry_menu=None) -> dict:
    """Şablondan bir örnek üretir, koşturur ve raporunu döner (spec §4 + K2 §6).

    Şablon çözümü iki katmanlı: önce derleme-anı TEMPLATES (her zaman kazanır),
    yoksa kayıt defteri (Kademe 2). Enjeksiyon noktaları (`model`, `instance`,
    `runner_factory`, `clock`, `templates`, `registry_lookup`, `registry_menu`)
    YALNIZCA testler içindir; üretimde hepsi None/varsayılan geçilir.

    FIRLATMAZ: bilinmeyen şablon menüye, kurulum hatası `durum="hata"`ya döner
    (İlke 4)."""
    known = TEMPLATES if templates is None else templates
    key = template_name.strip() if isinstance(template_name, str) else ""
    template = known.get(key)
    if template is None and key:
        template = (registry_lookup or _registry_template)(key)
    if template is None:
        logging.info("factory: bilinmeyen şablon istendi=%r", template_name)
        return unknown_template_reply(template_name,
                                      registered=(registry_menu or _registry_menu)())
```

(Gövdenin kalanı — instance üretimi, build_specialist, session, run_specialist —
DEĞİŞMEZ.)

`brain/app/tools.py` içindeki `spawn_specialist` docstring'ini güncelle
(yalnız docstring; gövde aynı):

```python
async def spawn_specialist(template: str, goal: str, tool_context) -> dict:
    """Dar kapsamlı, ÇOK ADIMLI bir işi geçici bir uzman ajana devreder ve
    sonucunu getirir. `template` ya sevkiyat kalıplarından biridir —
    "arastirmaci" (bir konuyu hafızadan ve izlenen repo'lardan toplayıp
    özetler), "arsivci" (bir konuşmadan çıkan kalıcı bilgiyi profile/derslere
    işler), "nobetci" (sistem sağlığını ve bekleyen işleri derleyip rapor
    eder) — ya da Kadir'in onayıyla kayıt defterine yazılmış KALICI bir ajanın
    adıdır (propose_agent ile önerilir). `goal` uzmana verilecek tek cümlelik
    Türkçe görevdir — ne istediğini SOMUT yaz, çünkü uzman senin sohbetini
    görmez. Uzman geçicidir: yalnızca kendi araç alt kümesini kullanır,
    bütçesi (adım + süre) sınırlıdır ve iş bitince ölür. Bilinmeyen bir şablon
    adı verirsen iki kaynaklı kullanılabilir şablon listesi döner. Tek adımda
    kendin yapabileceğin bir işi buraya devretme."""
```

- [ ] **Step 4: PASS gör + tam süit**

Run: `cd brain && python -m pytest tests/test_factory_registry.py tests/test_retro.py tests/test_factory.py -q && python -m pytest -q`
Expected: tümü yeşil. Özellikle `test_factory.py`'nin mevcut pinleri (junk template
menüsü dahil) DEĞİŞMEDEN geçmeli — `registry_lookup` üretim varsayılanı
`tools._memory is None` iken exception'ı yutup None döner, menü boş listeyle döner.

- [ ] **Step 5: Commit**

```bash
git add brain/app/factory.py brain/app/tools.py brain/tests/test_factory_registry.py brain/tests/test_retro.py
git commit -m "feat(factory): spawn resolves tier-2 registry templates

Live single-doc read at spawn time (production happens at approval time);
guest rules appended by the builder, documents revalidated fail-closed;
compile-time templates always shadow registry names; menu lists both sources.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Deploy + canlı kanıt (HITL — Kadir'le)

**Files:**
- Modify: yok (yalnız deploy + doğrulama)

**Interfaces:**
- Consumes: Task 1-4'ün tamamı main'de, tam süit yeşil.

- [ ] **Step 1: Tam süit + son kontrol**

Run: `cd brain && python -m pytest -q`
Expected: tümü yeşil (721 + ~40 yeni)

- [ ] **Step 2: İmajı kur ve deploy et** — projenin mevcut deploy süreciyle
(`brain/deploy/README` ne diyorsa o; YAML'lar `latestRevision: true` olmalı —
3 Ağu olayının dersi, dosyalarda zaten düzeltili). Cloud Build çağrısında
`--billing-project` tuzağına dikkat (LESSONS 4 Ağu).

- [ ] **Step 3: Deploy doğrulaması — "ayakta mı" değil "doğru sürüm mü"**

```bash
# trafik yeni revizyonda mı + imaj digest'i beklenen mi
gcloud run services describe jarvis-brain --region europe-west1 \
  --format="value(status.traffic)"
# uygulamanın kendi envanteri (3 Ağu standardı): yol sayısı değişmemeli (21)
curl -s https://<brain-url>/openapi.json | python -c "import json,sys; print(len(json.load(sys.stdin)['paths']))"
```

- [ ] **Step 4: Canlı kanıt zinciri (spec §10) — Kadir'le birlikte**

1. Sohbette Jarvis'ten bir kalıcı ajan önermesi istenir (veya doğal tetik beklenir) → S23'te kart: başlık `Kalıcı ajan '<ad>' kurulsun mu?`, detayda talimatın tam metni.
2. Kadir onaylar → Firestore `agent_registry/<ad>` dokümanı `granted`, `approval_id` kartla eşleşir.
3. `spawn_specialist(template="<ad>", ...)` gerçek koşusu sonuç döner; `audit_log`'da `actor=factory:<ad>#<örnek>` satırı görünür.
4. Negatif kontrol: kırmızı araç isteyen öneri Türkçe retle döner, kart/doküman doğmaz.

- [ ] **Step 5: Kapanış** — devir notu güncelle + mesh checkpoint + LESSONS'a ders (varsa) + memory dosyası (`jarvis-durum.md`: fabrika K2 CANLI).

---

## Self-Review Notları (plan yazarının kontrolü)

- **Spec kapsaması:** §3→Task 1, §4→Task 1+3, §5→Task 2+3, §6→Task 4, §7 revoke→Task 1 / retro→Task 4 pini, §8→Task 1-4 testleri, §10→Task 5. Boşluk yok.
- **Tip tutarlılığı:** `validate_definition` keyword-only ve tüm çağıranlar (propose_agent, grant, _template_from_doc) aynı ad kümesini geçiyor; `tool_names` list[str] her yerde; tavanlar int.
- **Bilinen riskler:** (1) `test_retro.py` pini mevcut dosyanın yardımcılarına uyarlanacak — uygulayıcı dosyayı OKUMADAN kopyalamasın; (2) `tools.py` import satırına `policy` eklemek döngü kurmamalı (policy tools'u import etmez — Task 3 Step 4 tam süitle kanıtlar); (3) FakeQuery `where` yalnız `==` destekliyor — `list_granted` tek eşitlik filtresi kullanır, uyumlu.
