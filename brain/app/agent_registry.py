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
# tools.propose_agent imzasındaki literal varsayılanlarla AYNI olmalı; ADK
# şeması literal int ister, o yüzden imza sabit REFERANS ALAMAZ — senkronu
# test_signature_defaults_match_the_registry_constants (pin testi) korur.
DEFAULT_MAX_STEPS = 24
DEFAULT_TTL_SECONDS = 90

# Ajan adı + factory'nin eklediği "factory_"/"#instance" ekleri okunabilir
# kalmalı (factory.build_specialist: f"factory_{name}_{instance}").
NAME_MAX = 64

# approvals._normalize_args her tool_args değerini 500 karakterde keser ve
# yürütücü şablonu kartın tool_args'ından kurar (spec §4.7). İşlevsel alanlar
# bu sınırın İÇİNDE kalmak zorunda; why/evidence belgeleyicidir, kesilebilir.
INSTRUCTION_MAX = 500
PURPOSE_MAX = 200
MAX_TOOLS = 8

# Misafir Kadir adına yetenek/ajan öneremez — §8.5 değişmez 1'in öneri
# düzlemi izdüşümü; recursion yasağı olan spawn dışlamasıyla (değişmez 3)
# aynı sınıf: kayıtlı ajan onay kuyruğuna kart kuramaz.
PROPOSAL_TOOL_NAMES = ("propose_tool", "propose_agent")

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
    if not name.isidentifier():
        return (f"Ajan adı boşluksuz bir tanımlayıcı olmalı (harf/rakam/alt çizgi): "
                f"'{name}' spawn edilemez.")
    if len(name) > NAME_MAX:
        return f"Ajan adı en fazla {NAME_MAX} karakter olabilir ({len(name)} verildi)."
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
        if tool_name in PROPOSAL_TOOL_NAMES:
            return ("Kayıtlı ajan Kadir adına öneri kuyruğuna kart kuramaz "
                    f"(§8.5 değişmez 1): '{tool_name}' araç listesine giremez.")
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
