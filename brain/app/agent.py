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
- Türkçe konuş; samimi ama profesyonel ol."""


def build_agent(
    memory: Memory, audit, model: "str | BaseLlm | None" = None, trust_provider=None
) -> Agent:
    """Build the jarvis_orchestrator agent. `model` defaults to config.MODEL_NAME
    (text chat); voice sessions pass config.resolve_live_model() instead -- same
    instruction/tools/policy, different model (see main.get_voice_runner_sessions_memory).
    ADK's Agent accepts either a model NAME (str) or a BaseLlm instance: the
    text runner passes whatever main._build_text_model() decided -- a plain
    string for the direct AI Studio path, or a Gemini instance bound to the
    local proxy's base_url when config.LLM_BASE_URL is set.

    `trust_provider` is passed ONLY by the voice runner (main._init_voice): it
    is how the voice bridge's identity/trust signals reach the policy matrix
    (see app/voice_trust.py). Leaving it None -- as the text runner does --
    keeps /api/chat's policy behaviour byte-identical."""
    tools.init(memory)
    return Agent(
        name=AGENT_NAME,
        model=model or config.MODEL_NAME,
        instruction=INSTRUCTION,
        tools=tools.ALL_TOOLS,
        before_tool_callback=make_policy_callback(audit, trust_provider=trust_provider),
    )
