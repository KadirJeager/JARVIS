import logging
import os

from . import live_model, voice_protocol

MODEL_NAME = os.environ.get("JARVIS_MODEL", "gemini-flash-latest")
# NOT a "-latest" native-audio alias on purpose: confirmed via live smoke test
# (task-2a4-report.md) that ADK 1.36.2's live connection layer
# (google/adk/models/gemini_llm_connection.py) buffers tool_call messages and
# only flushes them at turn_complete for any model where
# google.adk.utils.model_name_utils._is_gemini_3_x_live() is False -- which
# includes every gemini-2.x native-audio live model. Since turn_complete
# itself never arrives until the (buffered, never-yielded) tool call gets a
# response, every live turn that triggers a tool call deadlocks forever.
# Gemini 3.x Live models take the "yield tool calls immediately" fast path
# instead, so they don't hit this.
#
# No "-latest" alias exists yet for a Gemini 3.x Live model, so unlike
# MODEL_NAME above we can't just point at a Google-maintained alias.
# resolve_live_model() (task-2a6) does the equivalent job by hand: it fetches
# the live model catalog at voice-runner init and picks the newest usable
# one, so Jarvis follows Google's releases automatically instead of staying
# pinned to this dated preview forever. This constant is now ONLY the
# last-resort fallback used when resolution fails for any reason.
LIVE_MODEL_FALLBACK = os.environ.get("JARVIS_LIVE_MODEL", "gemini-3.1-flash-live-preview")


def resolve_live_model() -> str:
    """Resolve the live model to use for the voice runner.

    JARVIS_LIVE_MODEL, if set, is an absolute pin (testing/emergencies) that
    overrides auto-resolution entirely. Otherwise this delegates to
    live_model.resolve() -- fetch the live catalog, filter to usable
    candidates, pick the newest -- and falls back to LIVE_MODEL_FALLBACK on
    ANY failure (network, empty result, unexpected exception), logging the
    failure so it's visible without breaking voice-mode startup.
    """
    env_override = os.environ.get("JARVIS_LIVE_MODEL")
    if env_override:
        return env_override
    try:
        # Pass the same fallback so both failure branches (resolve()'s internal
        # empty/fetch-error path and this outer catch-all) stay in sync if the
        # pin is ever changed after an incident.
        return live_model.resolve(fallback=LIVE_MODEL_FALLBACK)
    except Exception:
        logging.exception(
            "config.resolve_live_model: live_model.resolve() failed, using fallback %s",
            LIVE_MODEL_FALLBACK,
        )
        return LIVE_MODEL_FALLBACK


DRY_RUN = os.environ.get("JARVIS_DRY_RUN", "0") == "1"
OAUTH_CLIENT_ID = os.environ.get("JARVIS_OAUTH_CLIENT_ID", "")
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
}
DEFAULT_ZONE = ZONE_RED  # unknown tool = red (safe default, §9)

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
# FLOOR for the turn_complete FALLBACK verification only (voice.py). That path
# fires when no finished input transcription arrived, so it has no positive
# signal that the buffer holds a whole utterance -- it can be room noise picked
# up after the real utterance was already scored and drained. Scoring a
# fragment against thresholds calibrated on ~3 s clips yields an arbitrary
# verdict, and an unverified verdict is not neutral: it fuses to LOW under
# locked/ambient. The transcription path deliberately has NO floor -- there
# Gemini has told us the utterance is complete, and short commands ("evet")
# must still be verified.
_DEFAULT_MIN_UTTERANCE_SECONDS = 0.5


def _effective_min_utterance_seconds(seconds: float) -> float:
    """Keep the floor inside [0, the rolling window], loudly.

    Its sibling JARVIS_SPEAKER_UTTERANCE_SECONDS is validated at load, and this
    one needs it for the same reason -- both ends misbehave silently:
    a negative value is a floor no buffer can be below (the guard is simply
    gone), and a value ABOVE the mic window is a floor no buffer can ever
    REACH, which kills the fallback outright: every turn whose finished
    transcription never arrives then passes unverified. That second one fails
    OPEN, so it must not be reachable by a typo."""
    if seconds < 0:
        logging.warning(
            "config: JARVIS_SPEAKER_MIN_UTTERANCE_SECONDS=%r is negative, which "
            "removes the fallback's fragment guard; falling back to %.1f s",
            seconds, _DEFAULT_MIN_UTTERANCE_SECONDS,
        )
        return _DEFAULT_MIN_UTTERANCE_SECONDS
    if _utterance_bytes(seconds) > SPEAKER_UTTERANCE_MAX_BYTES:
        logging.warning(
            "config: JARVIS_SPEAKER_MIN_UTTERANCE_SECONDS=%r exceeds the %.1f s mic "
            "window, so the turn_complete fallback could never fire; clamping to "
            "the window",
            seconds, SPEAKER_UTTERANCE_SECONDS,
        )
        return SPEAKER_UTTERANCE_SECONDS
    return seconds


SPEAKER_MIN_UTTERANCE_SECONDS = _effective_min_utterance_seconds(
    float(os.environ.get("JARVIS_SPEAKER_MIN_UTTERANCE_SECONDS",
                         _DEFAULT_MIN_UTTERANCE_SECONDS))
)
SPEAKER_MIN_UTTERANCE_BYTES = _utterance_bytes(SPEAKER_MIN_UTTERANCE_SECONDS)
# How much of the mic buffer a BARGE-IN keeps. A different concept from the
# floor above -- that one asks "is this enough audio to score?", this one asks
# "where did the barge-in utterance start?" -- and deliberately NOT derived from
# it, nor operator-tunable. The floor may legitimately be set to 0 (an opt-out),
# and voice.py trims with `del buf[:-onset]`, where `del buf[:-0]` is a NO-OP:
# sharing the constant would silently retire the trim and leave the model's
# whole speaking time in front of the utterance being scored. Same trap the mic
# window carries a guard for; it must not come back through a coupling. Clamped
# into (0, the mic window] so it is always both positive and reachable.
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
