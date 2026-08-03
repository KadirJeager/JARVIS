from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm

from . import config, tools
from .memory import Memory
from .policy import make_policy_callback

# Shared with app/main.py so rehydrated (cold-start) events are attributed to
# the SAME author ADK uses for live events (author=agent.name) -- see
# _ensure_session for why this matters (ADK's _is_other_agent_reply reframes
# any non-matching author as a third-party quote).
AGENT_NAME = "jarvis_orchestrator"

INSTRUCTION = """Sen Jarvis'sin — Kadir'in kişisel asistanı. Kendini her zaman \
"Kadir'in asistanı Jarvis" olarak tanıtırsın; Kadir'in yerine geçmezsin.

Davranış kuralları:
- Her oturumun başında get_user_profile aracını çağırıp Kadir'i tanı; cevaplarını profile göre kişiselleştir.
- Kadir bir tercih, rutin veya kural söylerse ("bundan sonra şöyle yap", "ben X'i severim") \
bunu remember_fact veya update_user_profile ile ANINDA kalıcılaştır ve kalıcılaştırdığını söyle.
- Bir hatan düzeltilirse add_lesson ile ders kaydet; benzer görevlerden önce search_memory ile geçmiş dersleri ara.
- Bir araç politika engeline takılırsa bunu Kadir'den saklama; ne yapmak istediğini ve neden \
engellendiğini açıkça söyle (hata = gözlem ilkesi).
- "Beni tanıyor musun" / "ses tanıma var mı" gibi sorularda get_speaker_status aracını çağır. \
Ses eşleşmesi bir risk sinyalidir, kesin kimlik kanıtı değildir: "sesinden tanıdım (skor %X)" \
diyebilirsin ama "ikinci faktörle doğruladım" gibi iddialarda bulunma.
- Oturum başında get_repo_updates aracını çağır; yeni olay varsa Türkçe, kısa özetle — kaynak linkiyle.
- Kırmızı bölge bir araç (ör. cancel_reminder) onay kartına düşerse Kadir'e "onay kartı gönderdim, \
karar verince yapacağım" de ve BEKLE. Aynı istek için kartı tekrar tekrar oluşturma; Kadir "evet, \
onaylıyorum" diye yazsa bile aracı yeniden çağırma — karar kartın üzerinden verilir, sohbette değil.
- Bir görev için elindeki araçlar YETMİYORSA bunu söyle ve propose_tool ile eksik yeteneği öner: \
gerekçeni Türkçe, somut yaz ("GitHub PR'larını okuyabilmem için gerekiyor"). En fazla sarı bölge \
iste; kırmızı bir yetenek önerilmez. Öneriden sonra BEKLE — kendi kendine kurmaya çalışma, aynı \
araç için ikinci kart oluşturma. Onaylanan araç bir sonraki açılışta etkin olur, o ana kadar \
elindeki araçlarla en iyisini yap.
- Türkçe konuş; samimi ama profesyonel ol."""


def build_agent(
    memory: Memory, audit, model: "str | BaseLlm | None" = None, trust_provider=None,
    approval_sink=None, zone_resolver=None, extra_toolsets=None,
) -> Agent:
    """Build the jarvis_orchestrator agent. `model` defaults to config.MODEL_NAME
    (text chat); the voice runner passes whatever main._build_text_model()
    decided -- same instruction/tools/policy, same model factory (voice turns
    are plain text turns since protocol v2; see main.get_voice_runner_sessions_memory).
    ADK's Agent accepts either a model NAME (str) or a BaseLlm instance: a plain
    string for the direct AI Studio path, or a Gemini instance bound to the
    local proxy's base_url when config.LLM_BASE_URL is set.

    `trust_provider` is passed ONLY by the voice runner (main._init_voice): it
    is how the voice bridge's identity/trust signals reach the policy matrix
    (see app/voice_trust.py). Leaving it None -- as the text runner does --
    keeps /api/chat's policy behaviour byte-identical.

    `approval_sink` (Faz Y3, spec §5) is what turns a RED block into a queued
    approval card. BOTH production runners get it (main._init and
    main._init_voice) -- red-zone tools have to be approvable from voice too.
    The guest gate never does: it does not go through this factory at all
    (§4.9, guests never reach RED). Left None the RED branch keeps its exact
    pre-Y3 text.

    `zone_resolver` (Faz Y4, spec §4.2) is how the tool registry reaches the
    policy matrix: it assigns zones to tool names the CODE does not know, and
    can never loosen one config.TOOL_ZONES already states (policy.check_zone).
    Both production runners get it; left None, zone resolution is pre-Y4.

    `extra_toolsets` (Faz Y4, spec §6) is the OTHER half of that story: the
    zone matrix knowing about a granted MCP server is worthless unless the
    server is actually attached. tool_registry.mcp_toolsets(db) builds these
    from the granted `kind="mcp"` records; both production runners pass them
    (main._init / _init_voice), each with its OWN toolset instances -- a
    toolset owns an MCP session and is closed with its agent, so the two
    runners must not share one. The guest gate never gets any: it does not go
    through this factory at all (§4.9).

    Left None or empty, `tools` is tools.ALL_TOOLS itself and the agent is
    byte-identical to the pre-Y4.1-task-3 one."""
    tools.init(memory)
    agent_tools = [*tools.ALL_TOOLS, *extra_toolsets] if extra_toolsets else tools.ALL_TOOLS
    return Agent(
        name=AGENT_NAME,
        model=model or config.MODEL_NAME,
        instruction=INSTRUCTION,
        tools=agent_tools,
        before_tool_callback=make_policy_callback(
            audit, trust_provider=trust_provider, approval_sink=approval_sink,
            zone_resolver=zone_resolver),
    )
