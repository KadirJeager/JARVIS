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
