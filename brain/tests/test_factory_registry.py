"""Fabrika Kademe 2 — kayıt defteri şablonlarıyla spawn (spec §6).

Taşıyıcı pimler:
- Kayıtlı şablonun instruction'ına _GUEST_RULES BUILDER'da eklenir (veriye güvenilmez).
- TEMPLATES kayıt defterini GÖLGELER (derleme anı her zaman kazanır).
- revoked/bozuk doküman fail-closed: spawn bilinmeyen şablon menüsüne düşer.
- Menü iki kaynağı etiketleriyle listeler.
- ÜRETİM okuma yolu (`_registry_template`/`_registry_menu` -> `agent_registry.get`/
  `list_granted` -> gerçek `tools._memory.db`) en az bir kez UÇTAN UCA koşar --
  yukarıdaki testlerin çoğu `registry_lookup`/`registry_menu` enjekte ederek bu
  yolu BAYPAS EDER (review bulgusu 2026-08-04: `agent_registry.get`'e ters
  argüman sırası ya da `doc.get("status")` karşılaştırmasının tersi, tüm süit
  yeşilken üretimde her kayıt-defteri spawn'ını sessizce kırardı).
"""
import pytest

from app import agent_registry, factory
from app import tools as tools_mod
from app.memory import Memory
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


# ---------------------------------------------------------------------------
# ÜRETİM YOLU — GERÇEK `agent_registry.get`/`list_granted` + `tools._memory.db`
# (yukarıdaki testler `registry_lookup`/`registry_menu` ikizleriyle bu okuma
# yolunu hiç koşturmuyordu; review bulgusu bunu kapatır).
# ---------------------------------------------------------------------------


@pytest.fixture
def _registry_db():
    """`tools._memory`'i gerçek bir FakeDB'ye bağlar (test_agent_proposals.py'nin
    `_db()` deseni), böylece `_registry_template`/`_registry_menu` PRODUCTION
    varsayılanlarıyla (enjeksiyonsuz) koşar. `tools._memory` süreç-geneli bir
    modül tekili olduğu için önceki değeri saklayıp testten sonra geri
    yüklüyoruz -- aksi hâlde bu dosyanın son testinin durumu başka test
    dosyalarına SIZAR (çalışma sırasına bağlı, kırılgan bir bağımlılık)."""
    previous = tools_mod._memory
    db = FakeDB()
    tools_mod.init(Memory(db))
    yield db
    tools_mod._memory = previous


def _grant_ozel_arastirmaci(db):
    return agent_registry.grant(
        db, name="ozel_arastirmaci", purpose="Özel araştırma.",
        instruction="Sen özel bir araştırmacısın.", tool_names=["search_memory"],
        max_steps=10, ttl_seconds=30, why="w", evidence="e", approval_id="ap1")


async def test_a_really_granted_agent_spawns_through_the_real_registry_read(_registry_db):
    """TAŞIYICI PİM (review bulgusu): `registry_lookup`/`registry_menu`
    ENJEKTE EDİLMEZ -- `spawn` üretim varsayılanlarına (`_registry_template`)
    düşer, o da `agent_registry.get(tools_mod._memory.db, ...)` ile GERÇEKTEN
    okur. Yukarıdaki testlerin hiçbiri `agent_registry.get`i hiç çağırmıyordu."""
    db = _registry_db
    outcome = _grant_ozel_arastirmaci(db)
    assert "yazıldı" in outcome

    result = await factory.spawn(
        "ozel_arastirmaci", "hedef", user_id=USER, audit=FakeAudit(),
        model="fake-model", instance="i1",
        runner_factory=_runner_factory(FakeRunner([FakeEvent("bitti")])),
        clock=lambda: 0.0)

    assert result["durum"] == factory.STATUS_OK
    assert result["sablon"] == "ozel_arastirmaci"
    assert result["sonuc"] == "bitti"


async def test_a_really_revoked_agent_falls_through_to_the_unknown_template_menu(_registry_db):
    """`_registry_template`'in `doc.get("status") != STATUS_GRANTED` dalını
    GERÇEK bir revoke ile tetikler -- `registry_lookup=lambda name: None`
    veren eski test bu dalı hiç koşturmuyordu, "bulunamadı" ile "revoked"i
    ayırt edemiyordu."""
    db = _registry_db
    _grant_ozel_arastirmaci(db)
    revoke_outcome = agent_registry.revoke(db, "ozel_arastirmaci")
    assert "geri alındı" in revoke_outcome

    result = await factory.spawn(
        "ozel_arastirmaci", "hedef", user_id=USER, audit=FakeAudit(),
        model="fake-model", instance="i1",
        runner_factory=_runner_factory(FakeRunner([FakeEvent("x")])),
        clock=lambda: 0.0)

    assert result["durum"] == factory.STATUS_ERROR
    assert "kullanilabilir_sablonlar" in result


async def test_the_real_menu_merges_shipped_and_registry_sources(_registry_db):
    """`_registry_menu()` GERÇEKTEN `agent_registry.list_granted`i çağırır ve
    `spawn`'ın enjeksiyonsuz bilinmeyen-şablon yanıtı iki kaynağı da taşır."""
    db = _registry_db
    _grant_ozel_arastirmaci(db)

    menu = factory._registry_menu()
    assert any(d.get("name") == "ozel_arastirmaci" for d in menu)

    result = await factory.spawn(
        "hayalet", "hedef", user_id=USER, audit=FakeAudit(), model="fake-model",
        instance="i1", runner_factory=_runner_factory(FakeRunner([FakeEvent("x")])),
        clock=lambda: 0.0)

    kaynaklar = {s.get("kaynak") for s in result["kullanilabilir_sablonlar"]}
    assert kaynaklar == {"sevkiyat", "kayit_defteri"}
    adlar = [s["ad"] for s in result["kullanilabilir_sablonlar"]]
    assert "ozel_arastirmaci" in adlar
