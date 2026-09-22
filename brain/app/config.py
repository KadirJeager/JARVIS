import logging
import os

from . import text_model, voice_protocol

MODEL_NAME = os.environ.get("JARVIS_MODEL", "gemini-flash-latest")


# Local LLM proxy (CLIProxyAPI) for the TEXT-chat path. Empty means the old
# behaviour: talk to AI Studio directly via the genai SDK default endpoint
# with MODEL_NAME. Set means main._build_text_model() binds the ADK Gemini
# object to this base_url instead -- and the model id is auto-resolved from
# the proxy's own catalog (see app/text_model.py for the "always newest
# usable flash" rule and the base_url "/v1beta" trap). Auth needs no code:
# the genai Client sends GOOGLE_API_KEY as x-goog-api-key either way, so in
# production the secret's CONTENT is simply the proxy key when this is set.
LLM_BASE_URL = os.environ.get("JARVIS_LLM_BASE_URL", "")
# NOT a pin -- the last-resort model id used only when catalog resolution
# fails for any reason (proxy down, empty/filtered-out catalog, unexpected
# exception).
TEXT_MODEL_FALLBACK = "gemini-3.6-flash-high"
# Voice turns go through the SAME text-turn machinery (protocol v2: STT/TTS
# on the device), but a voice conversation is real-time: the thinking-heavy
# "-high" variant the text resolver prefers costs seconds of dead air after
# every utterance (prod complaint 2026-07-31). The voice runner therefore
# resolves with latency_first=True -- fastest usable flash variant.
VOICE_MODEL_FALLBACK = "gemini-3.6-flash"

# Consultant-brain tools (North Star §4.9, app/consult.py). Same "not a pin"
# rule as TEXT_MODEL_FALLBACK: these ids are used ONLY when the live catalog
# cannot be fetched or holds nothing usable for that family -- normal
# resolution is dynamic (newest pro-class Gemini / newest claude-* from
# GET {base_url}/v1beta/models).
CONSULT_GEMINI_FALLBACK = "gemini-3.1-pro-preview"
CONSULT_CLAUDE_FALLBACK = "claude-opus-4-6"


def resolve_voice_model() -> str:
    """Resolve the model for the VOICE runner. Same rules as
    resolve_text_model() -- JARVIS_VOICE_MODEL (falling back to
    JARVIS_TEXT_MODEL) pins, no proxy means MODEL_NAME -- except the catalog
    resolution passes latency_first=True (see text_model.resolve's docstring
    for why) and the last-resort fallback is VOICE_MODEL_FALLBACK."""
    env_override = os.environ.get("JARVIS_VOICE_MODEL") or os.environ.get("JARVIS_TEXT_MODEL")
    if env_override:
        return env_override
    if not LLM_BASE_URL:
        return MODEL_NAME
    try:
        return text_model.resolve(fallback=VOICE_MODEL_FALLBACK, latency_first=True)
    except Exception:
        logging.exception(
            "config.resolve_voice_model: text_model.resolve() failed, using fallback %s",
            VOICE_MODEL_FALLBACK,
        )
        return VOICE_MODEL_FALLBACK


def resolve_text_model() -> str:
    """Resolve the model to use for the text-chat runner (and, since the v2
    voice protocol moved STT/TTS onto the device, for the voice runner too --
    see main._init_voice).

    JARVIS_TEXT_MODEL, if set, is an absolute pin (testing/emergencies) that
    overrides auto-resolution entirely. Otherwise, when LLM_BASE_URL is empty
    (no proxy configured) the resolution is trivial: MODEL_NAME, i.e. the
    direct AI Studio path keeps its "-latest" alias. When a proxy IS
    configured this delegates to text_model.resolve() -- fetch the proxy
    catalog, filter to usable flash candidates, pick the newest -- and falls
    back to TEXT_MODEL_FALLBACK on ANY failure (network, empty result,
    unexpected exception), logging the failure so it's visible without
    breaking text-mode startup.
    """
    env_override = os.environ.get("JARVIS_TEXT_MODEL")
    if env_override:
        return env_override
    if not LLM_BASE_URL:
        return MODEL_NAME
    try:
        # Pass the same fallback so both failure branches (resolve()'s internal
        # empty/fetch-error path and this outer catch-all) stay in sync if the
        # pin is ever changed after an incident.
        return text_model.resolve(fallback=TEXT_MODEL_FALLBACK)
    except Exception:
        logging.exception(
            "config.resolve_text_model: text_model.resolve() failed, using fallback %s",
            TEXT_MODEL_FALLBACK,
        )
        return TEXT_MODEL_FALLBACK


DRY_RUN = os.environ.get("JARVIS_DRY_RUN", "0") == "1"
OAUTH_CLIENT_ID = os.environ.get("JARVIS_OAUTH_CLIENT_ID", "")
# Cloud Scheduler OIDC (repo-watch job'u): service account e-postası + token
# audience'ı (servis URL'i). Boşsa /api/jobs/repo-watch 503 döner.
SCHEDULER_SA = os.environ.get("JARVIS_SCHEDULER_SA", "")
SCHEDULER_AUD = os.environ.get("JARVIS_SCHEDULER_AUD", "")

# Görev döngüsü (North Star §7.6, Faz Y1.3, app/tasks.py): görev kurulurken
# bütçe (adım tavanı) verilmezse kullanılan varsayılan — bütçesiz görev YOK.
# Token/para tavanı Y1.x işi; bu fazda tek bütçe türü adım sayısıdır.
TASKS_DEFAULT_MAX_STEPS = 50
ALLOWED_EMAILS = set(
    filter(None, os.environ.get("JARVIS_ALLOWED_EMAILS", "owner@example.com").split(","))
)

# Action authorization matrix (North Star §9) — tool name -> zone
ZONE_GREEN = "green"
ZONE_YELLOW = "yellow"
ZONE_RED = "red"

# Tool sensitivity tiers (North Star Phase C) — tool name -> tier
TIER_T0 = "t0"
TIER_T1 = "t1"
TIER_T2 = "t2"
TIER_T3 = "t3"

TOOL_ZONES = {
    "get_user_profile": ZONE_GREEN,
    "search_memory": ZONE_GREEN,
    "remember_fact": ZONE_GREEN,
    "add_lesson": ZONE_GREEN,
    "update_user_profile": ZONE_YELLOW,
    "get_speaker_status": ZONE_GREEN,
    "watch_repo": ZONE_YELLOW,
    "unwatch_repo": ZONE_YELLOW,
    "list_watched_repos": ZONE_GREEN,
    "get_repo_updates": ZONE_GREEN,
    # Consultant tools (§4.9) carry conversation content OUT to a second model
    # but change nothing local. §9 puts misafir danışma between green and red:
    # no write means not red, data egress means not green -- so YELLOW
    # ("yap + bildir": run, and Kadir sees in the audit that an outside brain
    # was consulted).
    "consult_gemini": ZONE_YELLOW,
    # consult_claude bilerek YOK (4 Ağu, ajan yüzeyinden kaldırıldı): tabloda
    # olmayan araç fail-closed KIRMIZIDIR, geri eklemek bilinçli bir karar olsun.
    # check_my_vitals (§4.5): salt okuma öz-rapor — kota sayaçları, process
    # gerçekleri, son politika engelleri. Yazma yok, dışa veri çıkışı yok.
    "check_my_vitals": ZONE_GREEN,
    # Reminders (§4.4, Faz Y2.4): sarı bölge ("yap + bildir") ve yeşil bölge (salt okuma)
    "set_reminder": ZONE_YELLOW,
    "list_reminders": ZONE_GREEN,
    # cancel_reminder (Faz Y3, spec §2): onay merkezinin ilk GERÇEK kırmızı
    # aracı — §9'un "bir şey silme" örneğinin en küçük, en zararsız hâli.
    # Bilinçli olarak muhafazakâr: §9 "eşikler konfigürasyondur; güven arttıkça
    # gevşetilebilir — kodda, sohbette değil". Mekanizma oturduktan sonra sarıya
    # alınabilir; o karar bu satırın değiştirilmesidir, modelin ikna edilmesi
    # değil.
    "cancel_reminder": ZONE_RED,
    # propose_tool (§8.5, Faz Y4.1): araç kazanım merdiveninin ilk basamağı.
    # YEŞİL, çünkü bir öneri kurmak zararsızdır — kayıt defterine yazmaz, hiçbir
    # yeteneği etkinleştirmez; asıl karar onay kartındadır ve onu Kadir verir.
    "propose_tool": ZONE_GREEN,
    "propose_agent": ZONE_GREEN,
    # spawn_specialist (§8.5, Faz Y4.2): fabrika Kademe 1'in çağrı yüzeyi.
    # SARI ("yap + bildir"): koşar ve Kadir audit'te/raporda görür. Kırmızı
    # olsaydı her devir bir onay kartı isterdi (kalıphanenin amacı buharlaşır);
    # yeşil olsaydı "bildir" yarısı zayıflardı — türetilmiş bir ajan koşturmak
    # sıradan bir okuma değildir. Örneğin KENDİ araçları ayrıca kendi
    # bölgelerinden geçer; bu bölge yalnızca DEVİR eylemini yetkilendirir.
    "spawn_specialist": ZONE_YELLOW,
}
DEFAULT_ZONE = ZONE_RED  # unknown tool = red (safe default, §9)

# Reversibility (Onay Kartı 2.0, Task 1) — tool name -> can-it-be-undone.
#
# Rule applied per tool:
#   reversible = True  when the effect lives ONLY inside JARVIS's own storage
#                       and another call can undo it (memory writes, task
#                       state, reminders, notes).
#   reversible = False when the effect leaves the system or cannot be
#                       recalled: messages, e-mail, orders, payments,
#                       deletions, anything involving a third party -- and
#                       anything ambiguous (fail-closed: when unsure, False).
#
# This is a fact about the tool, not a per-call inference -- it sits beside
# TOOL_ZONES so a new tool's zone and reversibility are declared together.
# The "every zoned tool" test below keeps the two tables from drifting apart.
TOOL_REVERSIBILITY = {
    "get_user_profile": True,        # read-only
    "search_memory": True,           # read-only
    "remember_fact": True,           # additive memory write (facts collection):
                                      # .add() only appends, nothing is overwritten
    "add_lesson": True,              # additive memory write (lessons collection):
                                      # .add() only appends, nothing is overwritten
    # update_user_profile does profile.set(patch, merge=True) (memory.py:117).
    # merge is TOP-LEVEL: every key in patch overwrites its previous value and
    # the old value is stored nowhere -- no call can restore it because JARVIS
    # never remembered it. That is the same argument that makes unwatch_repo
    # False: an internal-only effect that destroys prior state with no
    # recovery path is irreversible, contrast with remember_fact/add_lesson
    # above, whose .add() only appends and never overwrites.
    "update_user_profile": False,
    "get_speaker_status": True,      # read-only
    "watch_repo": True,              # creates a watch record, internal only
    # unwatch_repo deletes the Firestore watch doc outright (ref.delete()).
    # watch_repo can re-add a watch, but only with an EMPTY baseline (last_check,
    # etags all None per app/tools.py:117-128) -- so any release/commit that
    # landed during the gap is never surfaced. That is real information loss,
    # not just a discarded identity: irreversible.
    "unwatch_repo": False,
    "list_watched_repos": True,      # read-only
    # get_repo_updates flips each returned event's "surfaced" flag to True
    # (tools.py:192) so it isn't returned again. That flip is ONE-WAY -- no
    # tool un-surfaces an event -- but nothing is destroyed or sent outside
    # JARVIS, so the tool as a whole stays reversible.
    "get_repo_updates": True,
    # consult_gemini sends the question/context OUT to a third-party model.
    # Zone != reversibility: it writes nothing locally and its zone is only
    # YELLOW (no card required to run), but the content egress itself cannot
    # be recalled once it has left JARVIS -- irreversible regardless of zone.
    "consult_gemini": False,
    "check_my_vitals": True,         # read-only self-report
    "set_reminder": True,            # creates a reminder record, internal only
    "list_reminders": True,          # read-only
    # cancel_reminder deletes a reminder record -- explicitly the smallest
    # example of "deleting something" (see TOOL_ZONES comment above); RED
    # zone and irreversible agree here.
    "cancel_reminder": False,
    # propose_tool/propose_agent only write a pending approval request
    # (task state) -- they grant nothing by themselves and resolve via
    # Kadir's decision or TTL expiry, so the record itself is undoable.
    "propose_tool": True,
    "propose_agent": True,
    # spawn_specialist EXECUTES a bounded agent run, and that agent's tool
    # set is not confined to the safe stuff: agent_registry.validate_definition
    # hands a persistent agent every GREEN/YELLOW builtin, including
    # consult_gemini (agent_registry.py:120-136) -- and once running, those
    # inner calls fall to plain "allow" at HIGH trust (no separate approval
    # card per call). So this entry is the ONLY place the composite action's
    # reversibility is represented: a spawned agent can leak content to a
    # third party with nothing else in the system flagging it. Irreversible
    # even though the zone is only YELLOW.
    "spawn_specialist": False,
}
DEFAULT_REVERSIBILITY = False  # unknown tool = irreversible (fail-closed)


def is_reversible(tool_name: str) -> bool:
    """Resolve a tool's reversibility. Unknown tool fails closed to False.

    Unlike check_zone (policy.py), there is no zone_resolver here: a
    registry/MCP tool name (e.g. "github_mcp_list_prs") is not in
    TOOL_REVERSIBILITY and has no prefix-based resolver, so it silently
    falls to DEFAULT_REVERSIBILITY (False). Fail-closed and safe today, but
    after Task 4 it means every MCP tool's approval denies outright on
    timeout rather than getting the softer reversible-expiry treatment --
    a product decision, written down here rather than left to be discovered.
    """
    return TOOL_REVERSIBILITY.get(tool_name, DEFAULT_REVERSIBILITY)


# Operand argument (Onay Kartı 2.0, Task 2 fix round) -- tool name -> the
# ARGUMENT NAME (not the value) that carries Ç1's "the ONE concrete thing
# being acted on": the recipient, the file, the account. Explicit None for
# tools that act on nothing nameable (a search query, a free-text fact, a
# patch dict) -- there is no single identity to point at, and declaring None
# says so on purpose instead of leaving the entry to be guessed at a call
# site. Same shape as TOOL_REVERSIBILITY: a fact about the tool, declared
# once, beside its zone -- not a per-call inference. The "every zoned tool"
# test below (test_policy_tiers.py) keeps this table from drifting away from
# TOOL_ZONES the way TOOL_REVERSIBILITY's twin test does.
TOOL_OPERAND_ARG: dict[str, str | None] = {
    "get_user_profile": None,        # no arguments
    "search_memory": None,           # a broad query, not a single named target
    "remember_fact": None,           # free-text content, not a named target
    "add_lesson": None,              # free-text content, not a named target
    "update_user_profile": None,     # a patch dict, no single named target
    "get_speaker_status": None,      # no meaningful arguments
    "watch_repo": "repo",
    "unwatch_repo": "repo",
    "list_watched_repos": None,      # no arguments
    "get_repo_updates": None,        # no arguments
    "consult_gemini": None,          # a question, not a named target
    "check_my_vitals": None,         # no arguments
    "set_reminder": None,            # creates a new record; no existing target
    "list_reminders": None,          # no arguments
    "cancel_reminder": "reminder_id",
    # propose_tool/propose_agent: the proposed capability's own name is the
    # target. In practice these two are wired at their own call site
    # (tools.py propose_tool/propose_agent pass operand=name directly,
    # since they build a tool_grant/agent_grant record, not a tool_call);
    # this entry keeps the table complete and correct if a tool_call for
    # either name were ever routed through operand_of().
    "propose_tool": "name",
    "propose_agent": "name",
    "spawn_specialist": "template",
}


def operand_of(tool_name: str, args: dict | None) -> str | None:
    """Resolve Ç1's operand for a tool call: the ONE concrete thing an
    approval is bound to. Fail-closed to SILENCE, not a guess:

    - an undeclared tool (not in TOOL_OPERAND_ARG) -> None
    - a declared tool whose operand key is absent from `args` -> None

    NEVER falls back to "the first string argument" or `str(args)` -- the
    brief's own warning against that heuristic. A wrong operand is worse
    than a missing one: it makes an approval card look bound to something
    it is not, exactly when Kadir is relying on that row to decide.

    This function's contract is "a binding identifier or silence" -- two more
    cases fail closed to that same silence (Onay Kartı 2.0, Task 3 review
    carry-forward):

    - a declared key present but blank/whitespace-only after strip -> None,
      the same "permanently empty row" problem as an absent key, just
      spelled with an empty string instead of a missing one
    - a declared key holding a non-scalar (dict/list/...) -> None, never
      stringified: `str({...})`/`str([...])` produces Python repr noise, not
      an identifier, and unconditional stringification would silently pass
      that noise through as if it were one"""
    key = TOOL_OPERAND_ARG.get(tool_name)
    if key is None:
        return None
    value = (args or {}).get(key)
    if value is None or not isinstance(value, (str, int, float, bool)):
        return None
    return str(value).strip() or None


TOOL_TIERS = {
    # T0 (kanıt gerekmez: salt okuma / durum bilgisi)
    "list_watched_repos": TIER_T0,
    "get_repo_updates": TIER_T0,
    "list_reminders": TIER_T0,
    "check_my_vitals": TIER_T0,
    "get_speaker_status": TIER_T0,
    # T1 (düşük riskli yazma / yerel yapılandırma)
    "set_reminder": TIER_T1,
    "watch_repo": TIER_T1,
    "unwatch_repo": TIER_T1,
    # T2 (kişisel veri / dışa çıkış / beyin yazımı)
    "get_user_profile": TIER_T2,
    "search_memory": TIER_T2,
    "remember_fact": TIER_T2,
    "add_lesson": TIER_T2,
    "update_user_profile": TIER_T2,
    "consult_gemini": TIER_T2,
    "propose_tool": TIER_T2,
    "propose_agent": TIER_T2,
    "spawn_specialist": TIER_T2,
    # T3 (şimdilik hiçbir araç yok — enrollment endpoint'i bu seviyededir; REST)
    # cancel_reminder zaten RED; tier olarak da T3 (belge değeri)
    "cancel_reminder": TIER_T3,
}
DEFAULT_TIER = TIER_T2  # unknown tool = t2 (fail-closed)

# Onay merkezi (North Star §4.8, Faz Y3, app/approvals.py): bir onay kartının
# ömrü. Süre dolunca karar REDDEDİLİR — ve bu, süpürücü iş koşmasa bile karar
# anında uygulanır (spec §4.1). Kısa tutmak güvenli yöndür: süresi dolan onay
# kaybolmaz, model yeni bir kart oluşturabilir.
APPROVAL_TTL_MINUTES = int(os.environ.get("JARVIS_APPROVAL_TTL_MINUTES", "60"))

# Speaker identity (Katman 2b Dilim 3a) — cosine thresholds are starting
# estimates, calibrated after enrollment (spec §15).
SPEAKER_ACCEPT_THRESHOLD = float(os.environ.get("JARVIS_SPEAKER_ACCEPT", "0.35"))
SPEAKER_ADAPT_THRESHOLD = float(os.environ.get("JARVIS_SPEAKER_ADAPT", "0.60"))
SPEAKER_TOPK = int(os.environ.get("JARVIS_SPEAKER_TOPK", "3"))
SPEAKER_ADAPTIVE_CAP = int(os.environ.get("JARVIS_SPEAKER_ADAPTIVE_CAP", "20"))

# Anti-Spoofing / Deepfake Voice Detection (CM - Countermeasure, Phase B)
CM_ENABLED = os.environ.get("JARVIS_CM_ENABLED", "1") == "1"
CM_REJECT_THRESHOLD = float(os.environ.get("JARVIS_CM_REJECT_THRESHOLD", "0.85"))
CM_TIMEOUT_S = float(os.environ.get("JARVIS_CM_TIMEOUT_S", "8.0"))
# Inference cost control (prod evidence 2026-08-10: 3/3 utterances blew the 8 s
# budget on a 1 vCPU instance). Threads: empty = derive from the cgroup CPU quota
# (see antispoof._resolve_thread_count); set JARVIS_CM_TORCH_THREADS to override
# in prod without a rebuild. Seconds: caps the quadratic self-attention cost of a
# long utterance; the 6 Aug benchmark validated 3 s, this leaves margin.
CM_MAX_SECONDS = float(os.environ.get("JARVIS_CM_MAX_SECONDS", "4.0"))
CM_AUDIO_RATE = 16000
# Pre-load the CM model at startup instead of on the first utterance. OFF by
# default and switched on per service: the same image runs as jarvis-brain (which
# never scores audio) and jarvis-voice (which does), and the model's ~1.2 GiB
# does not fit the brain container. Prod evidence 2026-08-10: cold model load
# alone exceeded the 8 s CM budget, so the first utterance of every cold start
# lost its spoof verdict.
CM_WARMUP = os.environ.get("JARVIS_CM_WARMUP", "0") == "1"
# Enrollment writes ANCHORS -- immutable, un-evictable, and the reference that
# anchor_score refereeing depends on (app/speaker.py:99-106). A poisoned anchor
# is permanent, so this path fails CLOSED, unlike the live verify path which
# fails open so Kadir is never locked out mid-conversation.
CM_ENROLL_REQUIRED = os.environ.get("JARVIS_CM_ENROLL_REQUIRED", "1") == "1"
CM_MODEL_DIR = os.environ.get("CM_MODEL_DIR", "/opt/antispoof")
# Rolling mic-buffer window kept for the next speaker verification. The buffer
# is drained at a turn boundary, but a turn boundary is NOT guaranteed to
# arrive, so the window is what bounds memory (and inference time). 10 s is far
# more audio than ECAPA needs; the thresholds above were calibrated on ~3 s.
_DEFAULT_UTTERANCE_SECONDS = 10.0


def _utterance_bytes(seconds: float) -> int:
    """Seconds of PCM16 mono at the contract's input rate = 2 bytes per sample."""
    return int(seconds * voice_protocol.AUDIO_IN_RATE * 2)


def _effective_utterance_seconds(seconds: float) -> float:
    """Reject a window that would round down to a non-positive byte cap.

    A 0 (or negative, or sub-millisecond) setting does NOT "turn the cap off"
    in any useful sense: voice.py drains the buffer with `del buf[:-cap]`, and
    `del buf[:-0]` is `del buf[:0]` -- a NO-OP. The bound would silently vanish
    and the buffer would grow again at AUDIO_IN_RATE*2 = 32 KB/s (~115 MB/h)
    inside a process that already carries torch. Refuse the value loudly and
    keep the documented default so the invariant "the cap is positive" holds
    for every consumer of SPEAKER_UTTERANCE_MAX_BYTES."""
    if _utterance_bytes(seconds) > 0:
        return seconds
    logging.warning(
        "config: JARVIS_SPEAKER_UTTERANCE_SECONDS=%r yields a %d-byte mic window, "
        "which would disable the bound entirely; falling back to %.1f s",
        seconds, _utterance_bytes(seconds), _DEFAULT_UTTERANCE_SECONDS,
    )
    return _DEFAULT_UTTERANCE_SECONDS


SPEAKER_UTTERANCE_SECONDS = _effective_utterance_seconds(
    float(os.environ.get("JARVIS_SPEAKER_UTTERANCE_SECONDS", _DEFAULT_UTTERANCE_SECONDS))
)
SPEAKER_UTTERANCE_MAX_BYTES = _utterance_bytes(SPEAKER_UTTERANCE_SECONDS)
# How much of the mic buffer a SPEECH ONSET (the client's speech_start frame)
# keeps. Its own constant, deliberately NOT operator-tunable and NOT derived
# from any other knob: voice.py trims with `del buf[:-onset]`, and
# `del buf[:-0]` is `del buf[:0]` -- a NO-OP, so any coupling to a tunable
# value would silently retire the trim and leave whatever preceded the
# utterance (room noise, the interval since the last turn) in front of the
# audio being scored. Same trap the mic window carries a guard for; it must
# not come back through a coupling. Clamped into (0, the mic window] so it is
# always both positive and reachable.
SPEAKER_BARGE_IN_ONSET_BYTES = min(
    max(1, _utterance_bytes(0.5)), SPEAKER_UTTERANCE_MAX_BYTES
)

# Enroll batch bounds (M3, cleanup wave 2026-08-11): POST /api/voice/enroll's N clips
# share ONE CM_TIMEOUT_S budget (main.py's `asyncio.wait_for(... [antispoof.is_bonafide(pcm)
# for pcm in raw_clips] ...)` wraps the WHOLE batch), so an unbounded request doesn't fail
# loudly -- it just eventually 503s once the shared budget runs out. These make the
# contract explicit as two independent 400s instead.
ENROLL_MAX_CLIPS = int(os.environ.get("JARVIS_ENROLL_MAX_CLIPS", "10"))
# Per-clip byte cap: reuses SPEAKER_UTTERANCE_MAX_BYTES rather than inventing a second
# number. EnrollRequest.clips are documented (main.py's EnrollRequest) as the same PCM16
# mono 16kHz format SPEAKER_UTTERANCE_MAX_BYTES already bounds for the live utterance
# buffer, so its ~10 s ceiling is already a realistic "no legitimate single utterance is
# bigger than this" cap for one enroll clip too.
ENROLL_MAX_CLIP_BYTES = int(
    os.environ.get("JARVIS_ENROLL_MAX_CLIP_BYTES", str(SPEAKER_UTTERANCE_MAX_BYTES))
)

# Speaker identity MANAGEMENT (Katman 2b Dilim 3d). History cap bounds both
# cost and privacy exposure (spec §4.2); the manual cap is an ACCIDENT guard,
# not a security boundary -- the token holder can bypass voice entirely
# anyway (3a spec §12), the real guarantee is revocability (spec §5).
SPEAKER_HISTORY_CAP = int(os.environ.get("JARVIS_SPEAKER_HISTORY_CAP", "50"))
SPEAKER_MANUAL_CAP = int(os.environ.get("JARVIS_SPEAKER_MANUAL_CAP", "5"))
# Closed label set (spec §4.1): a fixed vocabulary is what makes aggregation
# possible ("gurultulu ortamda ortalama skor 0.41"); the free-text `note`
# field catches what the set misses. ASCII on purpose: these are API values,
# not UI copy. Revisited after threshold calibration (spec §12).
SPEAKER_SAMPLE_LABELS = frozenset(
    {"saglikli", "hasta", "yorgun", "gurultulu", "kulaklik", "hoparlor", "arac"}
)

TRUST_STATE_KEY = "trust_level"   # ADK session-state key policy._read_trust falls back to

# --- Vertex ses uçları (ses v3, 27 Ağu planı: docs/superpowers/plans/2026-08-27-ses-v3-f1-sse.md) ---
# STT: Vertex Gemini unary (WAV-wrapped PCM). TTS: Cloud TTS Gemini-TTS streaming.
# Kimlik: Cloud Run SA / lokal ADC (Workload Identity; key YOK). Proje: sabit —
# brain'in bütün GCP kaynakları your-gcp-project'te; quota project deploy yaml'inde pinli.
VERTEX_LOCATION = os.environ.get("JARVIS_VERTEX_LOCATION", "europe-west1")
VERTEX_PROJECT = os.environ.get("JARVIS_VERTEX_PROJECT", "your-gcp-project")

# STT: unary generate_content. Spike kanıtı (27 Ağu): gemini-2.5-flash 5.2 s
# sesi 2.72 s'de transkribe etti; prompt'taki terim listesi cihaz-üstü
# EXTRA_BIASING_STRINGS'in yerini alır ("Jarvis" artık doğru yazılıyor).
VERTEX_STT_MODEL = os.environ.get("JARVIS_VERTEX_STT_MODEL", "gemini-2.5-flash")
VERTEX_STT_TIMEOUT_S = float(os.environ.get("JARVIS_VERTEX_STT_TIMEOUT_S", "15.0"))
VERTEX_STT_PROMPT = os.environ.get(
    "JARVIS_VERTEX_STT_PROMPT",
    "Bu Türkçe konuşmayı aynen transkribe et. Sadece transkripti yaz; yorum, "
    "açıklama, çeviri ekleme. Kişi ve ürün adlarını doğru yaz: Jarvis, Kadir. "
    "Konuşma anlaşılmıyorsa ya da konuşma yoksa boş cevap ver.",
)

# TTS (Task 3): streaming_synthesize. Spike: 130 chunk, ilk chunk 1.19 s.
VERTEX_TTS_MODEL = os.environ.get("JARVIS_VERTEX_TTS_MODEL", "gemini-3.1-flash-tts-preview")
VERTEX_TTS_VOICE = os.environ.get("JARVIS_VERTEX_TTS_VOICE", "Kore")
VERTEX_TTS_LANG = os.environ.get("JARVIS_VERTEX_TTS_LANG", "tr-TR")
VERTEX_TTS_TIMEOUT_S = float(os.environ.get("JARVIS_VERTEX_TTS_TIMEOUT_S", "20.0"))

# Sunucu-taraflı VAD (proto 3): PCM16 RMS eşiği + sessizlik hangover'ı.
# Eşik 16-bit amplitüdde: sessiz oda <100, normal konuşma 1000+. Sahada ayarlanır;
# 500 kasıtlı olarak konuşma lehine (yanlış tetik bir boş transkripte mal olur,
# kaçırılan komut ise kullanıcıyı tekrar söyletir).
VAD_RMS_THRESHOLD = float(os.environ.get("JARVIS_VAD_RMS_THRESHOLD", "500"))
VAD_SILENCE_S = float(os.environ.get("JARVIS_VAD_SILENCE_S", "0.8"))
VAD_WATCHDOG_TICK_S = float(os.environ.get("JARVIS_VAD_WATCHDOG_TICK_S", "0.25"))
