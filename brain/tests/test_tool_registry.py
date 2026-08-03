"""app/tool_registry.py testleri (Faz Y4.1, Görev 1): kazanılmış yetenekler VERİ.

Taşıyıcı pim — silinirse Y4'ün güvenlik sınırı kalkar:

- `test_registry_cannot_loosen_a_code_zone` (spec §4.2): `config.TOOL_ZONES`'ta
  yazan bir araç için KOD kazanır. Bu pim olmadan onaylanmış tek bir kayıt
  `cancel_reminder`'ı yeşile çekip onay merkezini tamamen baypas ederdi.
- `test_check_zone_without_resolver_is_byte_identical` (regresyon pimi):
  çözücü verilmeyen her çağrı — guest_gate dahil — Y4 öncesiyle aynı cevabı
  verir.
"""
from app import config, policy, tool_registry
from app.guest_gate import GUEST_TOOL_NAMES
from tests.fakes import FakeDB

NOW = "2026-08-03T12:00:00+00:00"
LATER = "2026-08-03T13:00:00+00:00"


def _now():
    return NOW


def _later():
    return LATER


def _grant(db, name="github_mcp", kind=tool_registry.KIND_MCP, zone=config.ZONE_YELLOW,
           why="GitHub PR'larını okumam gerekiyor.", approval_id="a1", mcp=None, now_fn=_now):
    if mcp is None and kind == tool_registry.KIND_MCP:
        mcp = {"transport": "http", "url": "https://mcp.example/sse", "scopes": ["repo"]}
    return tool_registry.grant(db, name=name, kind=kind, zone=zone, why=why,
                               approval_id=approval_id, mcp=mcp, now_fn=now_fn)


def _doc(db, name):
    return db.collection(tool_registry.COLLECTION).document(name).get().to_dict()


# ---------------------------------------------------------------------------
# grant()
# ---------------------------------------------------------------------------


def test_grant_writes_granted_doc():
    db = FakeDB()
    out = _grant(db)
    doc = _doc(db, "github_mcp")
    assert doc["name"] == "github_mcp"
    assert doc["kind"] == tool_registry.KIND_MCP
    assert doc["zone"] == config.ZONE_YELLOW
    assert doc["status"] == tool_registry.STATUS_GRANTED
    assert doc["granted_at"] == NOW
    assert doc["revoked_at"] is None
    assert doc["approval_id"] == "a1"
    assert doc["mcp"]["url"] == "https://mcp.example/sse"
    assert "github_mcp" in out


def test_grant_twice_does_not_overwrite():
    """Aynı ad ikinci kez: create() AlreadyExists fırlatır -> Türkçe gözlem,
    var olan kayıt DEĞİŞMEZ (bölge/gerekçe ikinci öneriyle ezilemez)."""
    db = FakeDB()
    _grant(db, zone=config.ZONE_YELLOW, why="ilk gerekçe", approval_id="a1")
    out = _grant(db, zone=config.ZONE_GREEN, why="ikinci gerekçe", approval_id="a2")
    doc = _doc(db, "github_mcp")
    assert doc["zone"] == config.ZONE_YELLOW
    assert doc["why"] == "ilk gerekçe"
    assert doc["approval_id"] == "a1"
    assert "zaten" in out.lower()


def test_grant_rejects_red_zone():
    """§4.3: üretilmiş yetenek kırmızıya ASLA. Yürütücü de doğrular — öneri
    doğrulaması tek savunma hattı değildir."""
    db = FakeDB()
    out = _grant(db, zone=config.ZONE_RED)
    assert _doc(db, "github_mcp") is None or _doc(db, "github_mcp") == {}
    assert "kırmızı" in out.lower()


def test_grant_rejects_unknown_kind():
    db = FakeDB()
    out = _grant(db, kind="wasm", mcp=None)
    assert db.collection(tool_registry.COLLECTION).docs == {}
    assert "wasm" in out


def test_grant_rejects_empty_name():
    db = FakeDB()
    out = _grant(db, name="   ")
    assert db.collection(tool_registry.COLLECTION).docs == {}
    assert "ad" in out.lower()


# ---------------------------------------------------------------------------
# revoke()
# ---------------------------------------------------------------------------


def test_revoke_marks_revoked_and_keeps_the_doc():
    """§4.4: revoke SİLMEZ — audit izi (§8.5 değişmez 5) korunur."""
    db = FakeDB()
    _grant(db)
    out = tool_registry.revoke(db, "github_mcp", now_fn=_later)
    doc = _doc(db, "github_mcp")
    assert doc["status"] == tool_registry.STATUS_REVOKED
    assert doc["revoked_at"] == LATER
    assert doc["granted_at"] == NOW          # geçmiş silinmedi
    assert doc["approval_id"] == "a1"
    assert "github_mcp" in out


def test_revoke_unknown_name_is_an_observation_not_an_exception():
    db = FakeDB()
    out = tool_registry.revoke(db, "yok_boyle_bir_sey")
    assert "yok_boyle_bir_sey" in out


def test_grant_after_revoke_regrants_the_same_name():
    """§4.4: 'aynı ad yeniden önerilebilir'. Revoked bir kayıt create()'i
    AlreadyExists'e düşürür; bu dal yeniden kazandırmalı, yoksa iptal edilen bir
    yetenek bir daha ASLA geri alınamazdı."""
    db = FakeDB()
    _grant(db)
    tool_registry.revoke(db, "github_mcp", now_fn=_later)
    out = _grant(db, zone=config.ZONE_GREEN, why="tekrar gerekiyor", approval_id="a2",
                 now_fn=_later)
    doc = _doc(db, "github_mcp")
    assert doc["status"] == tool_registry.STATUS_GRANTED
    assert doc["zone"] == config.ZONE_GREEN
    assert doc["approval_id"] == "a2"
    assert doc["revoked_at"] is None
    assert "zaten" not in out.lower()


# ---------------------------------------------------------------------------
# get() / list_granted() / zone_for()
# ---------------------------------------------------------------------------


def test_list_granted_excludes_revoked():
    db = FakeDB()
    _grant(db, name="github_mcp")
    _grant(db, name="notion_mcp", approval_id="a2")
    tool_registry.revoke(db, "notion_mcp")
    names = [r["name"] for r in tool_registry.list_granted(db)]
    assert names == ["github_mcp"]


def test_get_returns_none_for_unknown_name():
    assert tool_registry.get(FakeDB(), "hic_yok") is None


def test_zone_for_granted_revoked_and_missing():
    db = FakeDB()
    _grant(db, zone=config.ZONE_YELLOW)
    assert tool_registry.zone_for(db, "github_mcp") == config.ZONE_YELLOW
    tool_registry.revoke(db, "github_mcp")
    assert tool_registry.zone_for(db, "github_mcp") is None
    assert tool_registry.zone_for(db, "hic_yok") is None


def test_zone_for_swallows_backend_failure():
    """Kayıt defteri okunamıyorsa None döner -> check_zone DEFAULT_ZONE (red).
    Bir Firestore hıçkırığı ASLA bir aracı açmamalı, ama ajanı da düşürmemeli."""

    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    assert tool_registry.zone_for(BoomDB(), "github_mcp") is None


# ---------------------------------------------------------------------------
# policy.check_zone × kayıt defteri
# ---------------------------------------------------------------------------


def test_check_zone_without_resolver_is_byte_identical():
    """REGRESYON PİMİ: çözücüsüz her çağrı (guest_gate dahil) Y4 öncesiyle aynı."""
    assert policy.check_zone("get_user_profile") == config.ZONE_GREEN
    assert policy.check_zone("update_user_profile") == config.ZONE_YELLOW
    assert policy.check_zone("cancel_reminder") == config.ZONE_RED
    assert policy.check_zone("launch_missiles") == config.ZONE_RED


def test_check_zone_resolves_unknown_tool_from_registry():
    db = FakeDB()
    _grant(db, name="github_mcp", zone=config.ZONE_YELLOW)
    resolver = tool_registry.make_zone_resolver(db)
    assert policy.check_zone("github_mcp", resolver) == config.ZONE_YELLOW


def test_check_zone_registry_miss_is_red():
    resolver = tool_registry.make_zone_resolver(FakeDB())
    assert policy.check_zone("github_mcp", resolver) == config.ZONE_RED


def test_check_zone_revoked_registry_entry_is_red():
    db = FakeDB()
    _grant(db, name="github_mcp", zone=config.ZONE_GREEN)
    tool_registry.revoke(db, "github_mcp")
    resolver = tool_registry.make_zone_resolver(db)
    assert policy.check_zone("github_mcp", resolver) == config.ZONE_RED


def test_registry_cannot_loosen_a_code_zone():
    """TAŞIYICI PİM (spec §4.2). Kayıt defteri `cancel_reminder`'ı green
    gösterse bile check_zone KODDAKİ red'i döner. Bu test silinirse/gevşerse
    onaylanmış tek bir kayıt onay merkezini baypas eder."""
    db = FakeDB()
    _grant(db, name="cancel_reminder", kind=tool_registry.KIND_BUILTIN,
           zone=config.ZONE_GREEN, mcp=None)
    resolver = tool_registry.make_zone_resolver(db)
    assert tool_registry.zone_for(db, "cancel_reminder") == config.ZONE_GREEN  # kayıt gerçekten var
    assert policy.check_zone("cancel_reminder", resolver) == config.ZONE_RED

    # Sarı bir araç da yeşile çekilemez.
    _grant(db, name="update_user_profile", kind=tool_registry.KIND_BUILTIN,
           zone=config.ZONE_GREEN, mcp=None, approval_id="a2")
    assert policy.check_zone("update_user_profile", resolver) == config.ZONE_YELLOW


def test_check_zone_rejects_garbage_zone_from_registry():
    """Bozuk/uydurma bir zone değeri red'e düşer (fail-closed)."""
    assert policy.check_zone("github_mcp", lambda name: "admin") == config.ZONE_RED


def test_check_zone_survives_a_throwing_resolver():
    def boom(name):
        raise RuntimeError("resolver patladı")

    assert policy.check_zone("github_mcp", boom) == config.ZONE_RED
    assert policy.check_zone("get_user_profile", boom) == config.ZONE_GREEN


def test_guest_gate_zones_are_unaffected_by_the_registry():
    """guest_gate `policy.check_zone(name)`'i ÇÖZÜCÜSÜZ çağırır ve tüm misafir
    araçları config.TOOL_ZONES'ta yazar: kayıt defteri ne yazarsa yazsın kapı
    aynı kararı verir (§4.9 — misafir yüzeyi kayıt defteriyle genişlemez)."""
    db = FakeDB()
    for name in GUEST_TOOL_NAMES:
        _grant(db, name=name, kind=tool_registry.KIND_BUILTIN, zone=config.ZONE_RED, mcp=None)
    resolver = tool_registry.make_zone_resolver(db)
    for name in GUEST_TOOL_NAMES:
        assert policy.check_zone(name) == config.ZONE_GREEN
        assert policy.check_zone(name, resolver) == config.ZONE_GREEN


# ---------------------------------------------------------------------------
# policy callback ucu
# ---------------------------------------------------------------------------


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


class _Tool:
    def __init__(self, name):
        self.name = name


def test_policy_callback_uses_the_zone_resolver():
    db = FakeDB()
    _grant(db, name="github_mcp", zone=config.ZONE_GREEN)
    audit = FakeAudit()
    cb = policy.make_policy_callback(
        audit, zone_resolver=tool_registry.make_zone_resolver(db))
    assert cb(_Tool("github_mcp"), {}, None) is None      # None => ADK aracı çalıştırır
    assert audit.entries[0]["zone"] == config.ZONE_GREEN
    assert audit.entries[0]["decision"] == "allow"


def test_policy_callback_without_resolver_still_blocks_unknown_tools():
    audit = FakeAudit()
    cb = policy.make_policy_callback(audit)
    result = cb(_Tool("github_mcp"), {}, None)
    assert result is not None and "POLİTİKA ENGELİ" in result["result"]
    assert audit.entries[0]["zone"] == config.ZONE_RED


# -- coverage pin (review minor 7) -------------------------------------------

def test_every_shipped_tool_has_an_explicit_zone_in_code():
    """Kod-önce sınırı yalnız config'in BİLDİĞİ adları korur.

    `check_zone` sırası TOOL_ZONES -> kayıt defteri -> DEFAULT_ZONE(red). Zone
    tablosuna yazılmayı unutulan bir araç bugün red olur (güvenli), AMA kayıt
    defteriyle green/yellow'a çekilebilir hâle gelir -- yani onay merkezini
    baypas etmenin yolu, yeni bir araca zone yazmayı unutmaktan geçer. Bu test o
    kapıyı kapatır: ALL_TOOLS'un her üyesi TOOL_ZONES'ta AÇIKÇA yer almalı."""
    from app.tools import ALL_TOOLS

    eksik = [fn.__name__ for fn in ALL_TOOLS if fn.__name__ not in config.TOOL_ZONES]
    assert not eksik, f"config.TOOL_ZONES'ta bölgesi yazılmamış araç(lar): {eksik}"
