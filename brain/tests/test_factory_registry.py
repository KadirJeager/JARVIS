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
