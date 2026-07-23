import os

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
# instead, so they don't hit this. No "-latest" alias exists yet for a
# Gemini 3.x Live model -- when one ships, prefer it over this dated preview.
LIVE_MODEL = os.environ.get("JARVIS_LIVE_MODEL", "gemini-3.1-flash-live-preview")
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
