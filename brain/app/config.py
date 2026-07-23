import logging
import os

from . import live_model

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
