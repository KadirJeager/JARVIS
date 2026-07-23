"""Auto-resolve the newest USABLE Gemini live model.

Kadir's "always latest" rule: the text path already tracks Google's newest
model automatically via the `gemini-flash-latest` alias (app/config.py's
MODEL_NAME). No such auto-tracking alias exists yet for live models, so this
module does the equivalent job by hand at voice-runner init time: fetch the
live model catalog, filter to what's actually usable, and pick the newest.

A model is USABLE here iff all three hold:
  1. It supports bidiGenerateContent (the live/bidi streaming API).
  2. It is deadlock-safe per ADK's OWN "Gemini 3.x Live" predicate -- see
     is_deadlock_safe() below for why this matters and where it's mirrored
     from.
  3. It is not task-specialized (translate/image/tts/vision/thinking-only
     variants aren't general-purpose voice-assistant models).

Verified against installed google-adk==1.36.2
(google/adk/utils/model_name_utils.py) on 2026-07-23 -- see task-2a6-report.md
for the exact source excerpt.
"""

import json
import logging
import os
import re
import urllib.request

_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_HTTP_TIMEOUT_SECONDS = 10

# Task-specialized live variants: not general-purpose voice-assistant models,
# even if they happen to pass the bidi + deadlock-safe filters.
_SPECIALIZED_MARKERS = ("translate", "image", "tts", "vision", "thinking")

# Fallback regex mirroring ADK's google.adk.utils.model_name_utils.
# _is_gemini_3_x_live EXACTLY (verified against installed google-adk 1.36.2
# source, 2026-07-23):
#
#   def _is_gemini_3_x_live(model_string):
#     if not model_string:
#       return False
#     model_name = extract_model_name(model_string)
#     return model_name.startswith('gemini-3.') and '-live' in model_name
#
# This is used ONLY if the ADK import below breaks (see is_deadlock_safe).
_GEMINI_3_X_LIVE_FALLBACK_RE = re.compile(r"^gemini-3\..*-live")


def _strip_models_prefix(name: str) -> str:
    """Mirror ADK's model_name_utils.extract_model_name() for the one shape
    we actually see from the models-list API: "models/<id>"."""
    if name.startswith("models/"):
        return name[len("models/") :]
    return name


def is_deadlock_safe(name: str) -> bool:
    """True iff `name` is a Gemini 3.x Live model per ADK's own predicate.

    ADK 1.36.2's live connection layer (google/adk/models/gemini_llm_connection.py)
    only takes the "yield tool_call parts immediately" fast path when
    google.adk.utils.model_name_utils._is_gemini_3_x_live() is True for the
    active model. Every other live model buffers tool_call messages until
    turn_complete -- which itself never arrives until the buffered tool call
    is answered, so any live turn that triggers a tool call deadlocks
    forever. See app/config.py's historical LIVE_MODEL comment and
    task-2a4-report.md for the original incident.

    We reuse ADK's OWN function (imported, not reimplemented) so that if
    ADK's definition of "3.x live" ever changes in a future version, this
    resolver tracks it automatically instead of silently drifting out of
    sync. The regex fallback only fires if the import path breaks (e.g. a
    future ADK release renames/removes the function).
    """
    try:
        from google.adk.utils.model_name_utils import _is_gemini_3_x_live

        return bool(_is_gemini_3_x_live(name))
    except ImportError:
        logging.warning(
            "live_model: google.adk.utils.model_name_utils._is_gemini_3_x_live "
            "not importable, using regex fallback"
        )
        if not name:
            return False
        model_name = _strip_models_prefix(name)
        return _GEMINI_3_X_LIVE_FALLBACK_RE.match(model_name) is not None


def is_specialized(name: str) -> bool:
    """True iff `name` looks task-specialized (translate/image/tts/vision/
    thinking-only), i.e. not a general-purpose voice-assistant model."""
    lowered = name.lower()
    return any(marker in lowered for marker in _SPECIALIZED_MARKERS)


def version_key(name: str) -> tuple:
    """Extract (major, minor) from a "gemini-<major>.<minor>..." model name
    for sort ordering. Unparseable input sorts lowest via (0, 0)."""
    model_name = _strip_models_prefix(name) if name else ""
    match = re.match(r"^gemini-(\d+)\.(\d+)", model_name)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def fetch_models() -> list:
    """Real HTTP fetch: GET /v1beta/models?key=$GOOGLE_API_KEY&pageSize=200.

    Returns the raw list of model dicts from the API's "models" field
    (each shaped like {"name": "models/...", "supportedGenerationMethods":
    [...]}). Raises on network/HTTP/JSON failure -- callers (resolve(),
    config.resolve_live_model()) are responsible for falling back.
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    url = f"{_MODELS_URL}?key={api_key}&pageSize=200"
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        body = response.read()
    data = json.loads(body)
    return data.get("models", [])


def resolve(fetch_models=fetch_models, fallback: str = "gemini-3.1-flash-live-preview") -> str:
    """Pick the newest USABLE live model, or `fallback` if none qualify.

    `fetch_models` is injectable for tests; it defaults to the real HTTP
    fetch (module-level fetch_models() above). Filters applied, in order:
    bidiGenerateContent support -> deadlock-safe (ADK 3.x-live predicate) ->
    not task-specialized. Among survivors, the highest version_key() wins;
    ties prefer a non-"preview" name, then fall back to lexical order.

    Logs a DATA-level line at INFO: how many models were fetched, how many
    survived the filters, and which one was ultimately chosen.
    """
    try:
        models = list(fetch_models())
    except Exception:
        logging.exception("live_model.resolve: fetch_models failed, using fallback %s", fallback)
        return fallback

    fetched_count = len(models)
    candidates = []
    for m in models:
        name = _strip_models_prefix(m.get("name", ""))
        if not name:
            continue
        methods = m.get("supportedGenerationMethods", [])
        if "bidiGenerateContent" not in methods:
            continue
        if not is_deadlock_safe(name):
            continue
        if is_specialized(name):
            continue
        candidates.append(name)

    passed_count = len(candidates)
    if not candidates:
        logging.info(
            "live_model.resolve: fetched=%d passed_filters=%d chosen=%s (fallback, no candidates)",
            fetched_count,
            passed_count,
            fallback,
        )
        return fallback

    def sort_key(name: str) -> tuple:
        # Higher version wins (max() picks the largest tuple). Among equal
        # versions we prefer non-preview names, so "is non-preview" must be
        # the LARGER value for the winner -- i.e. True (non-preview) beats
        # False (preview). Final tiebreak is lexical.
        return (version_key(name), "preview" not in name.lower(), name)

    chosen = max(candidates, key=sort_key)
    logging.info(
        "live_model.resolve: fetched=%d passed_filters=%d chosen=%s",
        fetched_count,
        passed_count,
        chosen,
    )
    return chosen
