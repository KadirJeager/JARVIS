"""Faz Y4.1 Görev 3: onaylı MCP sunucuları ajana GERÇEKTEN bağlanır.

Görev 1-2 kayıt defterini ve kazanım merdivenini kurdu ama `mcp_toolsets` boştu:
onaylanan bir MCP sunucusu Firestore'da bir satırdı, ajanda hiçbir şey. Bu dosya
o son mili pinler.

Üç taşıyıcı pim — silinirse Y4'ün MCP ayağı sessizce yarım kalır:

- `test_a_broken_record_does_not_silence_the_others` (spec §6): tek bozuk kayıt
  ajanı komple düşürmemeli. Bu pim olmadan yanlış yazılmış bir URL Jarvis'i
  tamamen susturur.
- `test_an_unmatched_mcp_tool_name_is_red` (spec §6, güvenli taraf): bir MCP
  sunucusundan gelen araç adları önceden BİLİNMEZ; eşleşme bulunamayan ad
  DEFAULT_ZONE'a (red) düşer. Bu pim olmadan bilinmeyen bir araç adı sessizce
  çalışır.
- `test_build_agent_without_extra_toolsets_is_unchanged` (regresyon pimi):
  `extra_toolsets` verilmeyen her çağrı Görev 3 öncesiyle BİREBİR aynı.

Ağ/süreç açan gerçek bir MCP sunucusu ÇALIŞTIRILMAZ: `McpToolset.__init__`
kaynaktan doğrulandığı üzere yalnızca bir `MCPSessionManager` kurar (bağlantı
`get_tools()` anında `create_session()` ile açılır), yani nesne kurulumu inert.
"""
import inspect
import types

import pytest
from google.adk.tools.mcp_tool import (McpToolset, SseConnectionParams,
                                       StdioConnectionParams,
                                       StreamableHTTPConnectionParams)

from app import config, policy, tool_registry, tools as tools_mod
from app.agent import build_agent
from app.memory import Memory
from tests.fakes import FakeDB

NOW = "2026-08-03T12:00:00+00:00"


def _now():
    return NOW


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


class _Tool:
    def __init__(self, name):
        self.name = name


def _http(url="https://mcp.example/mcp"):
    return {"transport": "http", "url": url, "command": None, "args": [], "scopes": ["repo"]}


def _stdio(command="npx", args=("-y", "@modelcontextprotocol/server-github")):
    return {"transport": "stdio", "url": None, "command": command, "args": list(args),
            "scopes": []}


def _grant(db, name, *, kind=tool_registry.KIND_MCP, zone=config.ZONE_YELLOW, mcp=None,
           approval_id=None):
    if mcp is None and kind == tool_registry.KIND_MCP:
        mcp = _http()
    return tool_registry.grant(db, name=name, kind=kind, zone=zone,
                               why="gerekçe", approval_id=approval_id or f"ap-{name}",
                               mcp=mcp, now_fn=_now)


# ---------------------------------------------------------------------------
# mcp_toolsets() — hangi kayıtlardan toolset kurulur
# ---------------------------------------------------------------------------


def test_builds_one_toolset_per_granted_mcp_record():
    db = FakeDB()
    _grant(db, "github_mcp")
    _grant(db, "notion_mcp", mcp=_stdio())
    seen = []

    def factory(record):
        seen.append(record["name"])
        return f"toolset:{record['name']}"

    out = tool_registry.mcp_toolsets(db, toolset_factory=factory)

    assert sorted(seen) == ["github_mcp", "notion_mcp"]
    assert sorted(out) == ["toolset:github_mcp", "toolset:notion_mcp"]


def test_builtin_and_revoked_records_are_skipped():
    """Yalnız `kind="mcp"` VE `status="granted"` kayıtlar bağlanır. Bir builtin
    kaydı MCP taşıması taşımaz; iptal edilmiş bir sunucu bağlanırsa revoke hiçbir
    şey ifade etmezdi."""
    db = FakeDB()
    _grant(db, "github_mcp")
    _grant(db, "get_user_profile", kind=tool_registry.KIND_BUILTIN, mcp=None)
    _grant(db, "notion_mcp", mcp=_stdio())
    tool_registry.revoke(db, "notion_mcp")

    names = [r["name"] for r in _records_passed_to_factory(db)]

    assert names == ["github_mcp"]


def _records_passed_to_factory(db):
    seen = []
    tool_registry.mcp_toolsets(db, toolset_factory=lambda record: seen.append(record) or "ts")
    return seen


def test_empty_registry_yields_no_toolsets():
    assert tool_registry.mcp_toolsets(FakeDB(), toolset_factory=lambda r: "ts") == []


# ---------------------------------------------------------------------------
# HATA İZOLASYONU (spec §6) — TAŞIYICI PİM
# ---------------------------------------------------------------------------


def test_a_broken_record_does_not_silence_the_others(caplog):
    """TAŞIYICI PİM (spec §6): 'bir MCP sunucusu ayağa kalkmazsa ajan YİNE
    kurulmalıdır — o toolset atlanır ve loglanır.'

    Bu izolasyon kaldırılırsa yanlış yazılmış tek bir URL Jarvis'i komple
    susturur: build_agent fırlatır, runner kurulmaz, hiçbir araç çalışmaz."""
    db = FakeDB()
    _grant(db, "aaa_ok")
    _grant(db, "bbb_bozuk")
    _grant(db, "ccc_ok")

    def factory(record):
        if record["name"] == "bbb_bozuk":
            raise RuntimeError("MCP sunucusu ayağa kalkmadı")
        return f"toolset:{record['name']}"

    out = tool_registry.mcp_toolsets(db, toolset_factory=factory)

    assert sorted(out) == ["toolset:aaa_ok", "toolset:ccc_ok"]
    assert "bbb_bozuk" in caplog.text


def test_every_record_broken_still_returns_a_list():
    db = FakeDB()
    _grant(db, "aaa_bozuk")
    _grant(db, "bbb_bozuk")

    def boom(record):
        raise ValueError("hepsi bozuk")

    assert tool_registry.mcp_toolsets(db, toolset_factory=boom) == []


def test_an_unreadable_registry_yields_no_toolsets_instead_of_raising():
    """Firestore erişilemezse ajan MCP'siz kurulur — ama kurulur."""

    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    assert tool_registry.mcp_toolsets(BoomDB(), toolset_factory=lambda r: "ts") == []


# ---------------------------------------------------------------------------
# Gerçek ADK toolset kurulumu (google-adk 1.36.2 imzaları kaynaktan doğrulandı)
# ---------------------------------------------------------------------------


def test_http_record_builds_streamable_http_params():
    ts = tool_registry.build_mcp_toolset(
        {"name": "github_mcp", "mcp": _http("https://mcp.example/mcp")})

    assert isinstance(ts, McpToolset)
    assert isinstance(ts._connection_params, StreamableHTTPConnectionParams)
    assert ts._connection_params.url == "https://mcp.example/mcp"
    assert ts.tool_name_prefix == "github_mcp"


def test_sse_record_builds_sse_params():
    ts = tool_registry.build_mcp_toolset(
        {"name": "sse_mcp", "mcp": {"transport": "sse", "url": "https://mcp.example/sse"}})

    assert isinstance(ts._connection_params, SseConnectionParams)
    assert ts._connection_params.url == "https://mcp.example/sse"


def test_stdio_record_builds_stdio_params():
    ts = tool_registry.build_mcp_toolset(
        {"name": "local_mcp", "mcp": _stdio("npx", ("-y", "server-github"))})

    assert isinstance(ts._connection_params, StdioConnectionParams)
    assert ts._connection_params.server_params.command == "npx"
    assert ts._connection_params.server_params.args == ["-y", "server-github"]
    assert ts.tool_name_prefix == "local_mcp"


def test_the_toolset_prefix_is_the_registry_name():
    """Bölge eşlemesi bu ön-eke dayanır (aşağıdaki zone testleri). ADK'nın
    BaseToolset.get_tools_with_prefix'i araç adını `f"{prefix}_{tool.name}"`
    yapar — kaynaktan doğrulandı. Ön-ek kaydın adı olmazsa gelen araçların
    bölgesi çözülemez ve hepsi kırmızıya düşer."""
    ts = tool_registry.build_mcp_toolset({"name": "github_mcp", "mcp": _http()})
    assert ts.tool_name_prefix == "github_mcp"


@pytest.mark.parametrize("mcp", [
    None,
    {},
    {"transport": "http", "url": "", "command": None},
    {"transport": "stdio", "command": "", "args": []},
    {"transport": "carrier_pigeon", "url": "https://mcp.example/mcp"},
])
def test_a_record_without_a_usable_transport_raises(mcp):
    """Fırlatması İSTENEN davranış: `mcp_toolsets` bunu yakalayıp kaydı atlar
    (yukarıdaki izolasyon pimi), yani bozuk taşıma tanımı sessizce YANLIŞ bir
    toolset kurmaz."""
    with pytest.raises(ValueError):
        tool_registry.build_mcp_toolset({"name": "bozuk", "mcp": mcp})


def test_a_broken_transport_record_is_skipped_end_to_end():
    """Gerçek fabrikayla (enjeksiyonsuz): bozuk kayıt atlanır, sağlam kurulur."""
    db = FakeDB()
    _grant(db, "iyi_mcp", mcp=_http())
    _grant(db, "bozuk_mcp", mcp={"transport": "carrier_pigeon"})

    out = tool_registry.mcp_toolsets(db)

    assert len(out) == 1
    assert out[0].tool_name_prefix == "iyi_mcp"


# ---------------------------------------------------------------------------
# build_agent(extra_toolsets=...)
# ---------------------------------------------------------------------------


def test_build_agent_without_extra_toolsets_is_unchanged():
    """REGRESYON PİMİ: `extra_toolsets` verilmeyen her çağrı Görev 3 öncesiyle
    BİREBİR aynı araç listesini kurar."""
    agent = build_agent(Memory(FakeDB()), FakeAudit())
    assert list(agent.tools) == list(tools_mod.ALL_TOOLS)


def test_build_agent_appends_extra_toolsets_after_all_tools():
    ts = tool_registry.build_mcp_toolset({"name": "github_mcp", "mcp": _http()})
    agent = build_agent(Memory(FakeDB()), FakeAudit(), extra_toolsets=[ts])

    assert list(agent.tools)[:len(tools_mod.ALL_TOOLS)] == list(tools_mod.ALL_TOOLS)
    assert list(agent.tools)[len(tools_mod.ALL_TOOLS):] == [ts]


def test_build_agent_does_not_mutate_all_tools():
    ts = tool_registry.build_mcp_toolset({"name": "github_mcp", "mcp": _http()})
    before = list(tools_mod.ALL_TOOLS)
    build_agent(Memory(FakeDB()), FakeAudit(), extra_toolsets=[ts])
    assert list(tools_mod.ALL_TOOLS) == before


# ---------------------------------------------------------------------------
# BÖLGE (spec §6): MCP araç adları önceden bilinmez -> ön-ek eşlemesi
# ---------------------------------------------------------------------------


def test_a_prefixed_mcp_tool_name_inherits_the_server_zone():
    """Kaydın zone'u o sunucunun TÜM araçlarına uygulanır (spec §6)."""
    db = FakeDB()
    _grant(db, "github_mcp", zone=config.ZONE_YELLOW)
    resolver = tool_registry.make_zone_resolver(db)

    assert tool_registry.zone_for(db, "github_mcp_list_pull_requests") == config.ZONE_YELLOW
    assert policy.check_zone("github_mcp_list_pull_requests", resolver) == config.ZONE_YELLOW
    assert policy.check_zone("github_mcp_create_issue", resolver) == config.ZONE_YELLOW


def test_an_unmatched_mcp_tool_name_is_red():
    """TAŞIYICI PİM (spec §6): 'Bilinmiyorsa DEFAULT_ZONE (red) — güvenli taraf.'

    Bu pim gevşetilirse bir MCP sunucusundan gelen HERHANGİ bir ad politika
    matrisini baypas eder."""
    db = FakeDB()
    _grant(db, "github_mcp", zone=config.ZONE_GREEN)
    resolver = tool_registry.make_zone_resolver(db)

    assert policy.check_zone("notion_mcp_search", resolver) == config.ZONE_RED
    assert policy.check_zone("rm_rf", resolver) == config.ZONE_RED
    # Ön-ek eşleşmesi `_` ayıracını ZORUNLU kılar: ADK adı `f"{prefix}_{ad}"`
    # yapar, dolayısıyla 'github_mcpanything' o sunucudan GELEMEZ.
    assert policy.check_zone("github_mcpanything", resolver) == config.ZONE_RED
    # Çıplak sunucu adının kendisi de bir araç adı değildir ama kaydı vardır:
    # onun bölgesi zaten tam eşleşmeyle çözülür.
    assert policy.check_zone("github_mcp", resolver) == config.ZONE_GREEN


def test_a_revoked_server_stops_lending_its_zone():
    db = FakeDB()
    _grant(db, "github_mcp", zone=config.ZONE_GREEN)
    resolver = tool_registry.make_zone_resolver(db)
    assert policy.check_zone("github_mcp_list_prs", resolver) == config.ZONE_GREEN

    tool_registry.revoke(db, "github_mcp")

    assert policy.check_zone("github_mcp_list_prs", resolver) == config.ZONE_RED


def test_a_builtin_record_does_not_lend_its_zone_by_prefix():
    """Ön-ek yalnız MCP kayıtları içindir: builtin bir kayıt tek bir aracın
    adıdır, bir isim uzayı değil. Aksi hâlde `get_user_profile` (green) kaydı
    `get_user_profile_and_wipe_disk`'i de yeşile çekerdi."""
    db = FakeDB()
    _grant(db, "some_builtin", kind=tool_registry.KIND_BUILTIN, zone=config.ZONE_GREEN, mcp=None)
    resolver = tool_registry.make_zone_resolver(db)

    assert policy.check_zone("some_builtin", resolver) == config.ZONE_GREEN
    assert policy.check_zone("some_builtin_and_more", resolver) == config.ZONE_RED


def test_code_zones_still_win_over_an_mcp_prefix():
    """TAŞIYICI PİM'in ön-ek uzantısı (spec §4.2): `cancel` adlı yeşil bir MCP
    sunucusu `cancel_reminder`'ı yeşile ÇEKEMEZ — kod her zaman önce gelir."""
    db = FakeDB()
    _grant(db, "cancel", zone=config.ZONE_GREEN)
    resolver = tool_registry.make_zone_resolver(db)

    assert policy.check_zone("cancel_reminder", resolver) == config.ZONE_RED
    assert policy.check_zone("update_user_profile", resolver) == config.ZONE_YELLOW


@pytest.mark.parametrize("kisa_zone,uzun_zone", [
    (config.ZONE_YELLOW, config.ZONE_GREEN),   # kısıtlayıcı olan ÖNCE sıralanır
    (config.ZONE_GREEN, config.ZONE_YELLOW),   # kısıtlayıcı olan SONRA sıralanır
])
def test_ambiguous_prefixes_resolve_to_the_most_restrictive_zone(kisa_zone, uzun_zone):
    """İki kayıt aynı adı ön-ekliyorsa hangisinden geldiği BİLİNEMEZ
    ('github' + 'mcp_list' mi, 'github_mcp' + 'list' mi?). Güvenli taraf: en
    kısıtlayıcı bölge kazanır.

    İki sıralama da denenir ÇÜNKÜ `list_granted` ada göre sıralı döner: tek
    yönle yazılan bir test, "ilk eşleşme kazanır" gibi yanlış bir uygulamayı da
    yeşil gösterirdi (bu mutasyon gerçekten kaçtı, test bu yüzden çiftlendi)."""
    db = FakeDB()
    _grant(db, "github", zone=kisa_zone)
    _grant(db, "github_mcp", zone=uzun_zone)
    resolver = tool_registry.make_zone_resolver(db)

    assert policy.check_zone("github_mcp_list", resolver) == config.ZONE_YELLOW


def test_prefix_resolution_swallows_a_backend_failure():
    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore down")

    assert tool_registry.zone_for(BoomDB(), "github_mcp_list") is None
    assert policy.check_zone(
        "github_mcp_list", tool_registry.make_zone_resolver(BoomDB())) == config.ZONE_RED


def test_the_policy_callback_blocks_an_unmatched_mcp_tool():
    """Uçtan uca: eşleşmeyen bir MCP aracı gerçekten ENGELLENİR (callback None
    DEĞİL bir sonuç döner => ADK aracı çalıştırmaz)."""
    db = FakeDB()
    _grant(db, "github_mcp", zone=config.ZONE_GREEN)
    audit = FakeAudit()
    cb = policy.make_policy_callback(audit, zone_resolver=tool_registry.make_zone_resolver(db))

    assert cb(_Tool("github_mcp_list_prs"), {}, None) is None       # granted sunucu -> geçer
    blocked = cb(_Tool("notion_mcp_search"), {}, None)
    assert blocked is not None and "POLİTİKA ENGELİ" in blocked["result"]
    assert audit.entries[-1]["zone"] == config.ZONE_RED


# ---------------------------------------------------------------------------
# Üretim bağlantısı: main._init / _init_voice  (ve guest_gate'in ASLA bağlanmaması)
# ---------------------------------------------------------------------------


@pytest.fixture()
def production_init(monkeypatch):
    """main._init()/_init_voice()'u GERÇEKTEN koşturur; yalnız dış bağımlılıklar
    sahtelenir. tests/test_approvals_api.py'deki aynı desen."""
    import app.agent as agent_mod
    import app.main as main_mod
    import app.memory as memory_mod
    from google.adk.sessions import InMemorySessionService
    from google.cloud import firestore

    db = FakeDB()
    monkeypatch.setattr(firestore, "Client", lambda *a, **k: db)
    monkeypatch.setattr(memory_mod, "make_e5_embedders", lambda: types.SimpleNamespace(
        embed_passage=lambda text: [0.0], embed_query=lambda text: [0.0]))
    monkeypatch.setattr(main_mod.config, "LLM_BASE_URL", "")
    monkeypatch.setattr(main_mod.config, "resolve_text_model", lambda: "fake-text-model")
    monkeypatch.setattr(main_mod.config, "resolve_voice_model", lambda: "fake-voice-model")

    calls = []
    real_build_agent = agent_mod.build_agent

    def capturing(*args, **kwargs):
        calls.append((args, kwargs))
        return real_build_agent(*args, **kwargs)

    monkeypatch.setattr(agent_mod, "build_agent", capturing)
    for name in ("_runner", "_voice_runner", "_memory", "_messages", "_conversations",
                 "_speaker_service"):
        monkeypatch.setattr(main_mod, name, None)
    monkeypatch.setattr(main_mod, "_session_service", InMemorySessionService())
    return main_mod, calls, db


def _bound(call):
    args, kwargs = call
    bound = inspect.signature(build_agent).bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


def test_both_production_runners_attach_granted_mcp_servers(production_init):
    """Son mil: `extra_toolsets=` anahtarını main._init/_init_voice'tan silmek,
    onaylanan MCP sunucusunu yine Firestore'da bir satır olarak bırakır — bu
    test o silmeyi yakalar (metin ve ses runner'larını ayrı ayrı)."""
    main_mod, calls, db = production_init
    _grant(db, "github_mcp", zone=config.ZONE_YELLOW)

    main_mod._init_voice()      # önce _init(), sonra ses runner'ı

    assert len(calls) == 2, f"iki build_agent çağrısı bekleniyordu, {len(calls)} geldi"
    for label, call in zip(("METİN", "SES"), (_bound(c) for c in calls)):
        toolsets = call["extra_toolsets"]
        assert toolsets, f"{label} runner'ı MCP toolset'siz kuruldu"
        assert [ts.tool_name_prefix for ts in toolsets] == ["github_mcp"], label


def test_production_runners_are_built_even_with_a_broken_mcp_record(production_init):
    """Bozuk bir kayıt üretim yolunu da düşürmez: runner kurulur, sağlam
    sunucular bağlanır."""
    main_mod, _calls, db = production_init
    _grant(db, "iyi_mcp", mcp=_http())
    _grant(db, "bozuk_mcp", mcp={"transport": "carrier_pigeon"})

    main_mod._init()

    prefixes = [t.tool_name_prefix for t in main_mod._runner.agent.tools
                if isinstance(t, McpToolset)]
    assert prefixes == ["iyi_mcp"]


def test_an_empty_registry_leaves_the_production_tool_list_unchanged(production_init):
    """Kayıt defteri boşken üretim ajanı Görev 3 öncesiyle birebir aynı."""
    main_mod, _calls, _db = production_init
    main_mod._init()
    assert list(main_mod._runner.agent.tools) == list(tools_mod.ALL_TOOLS)


async def test_the_guest_gate_never_gains_a_granted_mcp_server():
    """§4.9: misafir yüzeyi kayıt defteriyle GENİŞLEMEZ. Misafir kapısı
    build_agent'tan geçmez; araç listesi `_GUEST_TOOL_SPECS`'te sabittir."""
    from app import guest_gate

    db = FakeDB()
    _grant(db, "github_mcp", zone=config.ZONE_YELLOW)

    served = {t.name for t in await guest_gate._build_mcp().list_tools()}

    assert served == set(guest_gate.GUEST_TOOL_NAMES)
    assert not any(name.startswith("github_mcp") for name in served)
