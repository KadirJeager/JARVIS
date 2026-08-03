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
    "consult_claude": ZONE_YELLOW,
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
    # spawn_specialist (§8.5, Faz Y4.2): fabrika Kademe 1'in çağrı yüzeyi.
    # SARI ("yap + bildir"): koşar ve Kadir audit'te/raporda görür. Kırmızı
    # olsaydı her devir bir onay kartı isterdi (kalıphanenin amacı buharlaşır);
    # yeşil olsaydı "bildir" yarısı zayıflardı — türetilmiş bir ajan koşturmak
    # sıradan bir okuma değildir. Örneğin KENDİ araçları ayrıca kendi
    # bölgelerinden geçer; bu bölge yalnızca DEVİR eylemini yetkilendirir.
    "spawn_specialist": ZONE_YELLOW,
}
DEFAULT_ZONE = ZONE_RED  # unknown tool = red (safe default, §9)

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
