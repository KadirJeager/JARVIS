"""Auto-resolve the newest USABLE Gemini flash text-chat model from a local
CLIProxyAPI proxy catalog.

Kadir's "always latest" rule, carried into the proxy world: when the text
path talks to a local LLM proxy (config.LLM_BASE_URL set) instead of AI
Studio, Google's `gemini-flash-latest` alias no longer applies -- the proxy
serves its own catalog of explicitly versioned models. This module does the
same job live_model.py does for voice: fetch the catalog at runner init,
filter to what is actually usable for general-purpose text chat, and pick
the newest, so Jarvis follows the proxy's model upgrades automatically.

A model is USABLE here iff all four hold:
  1. Its id starts with "gemini-" (the proxy also serves OTHER providers'
     models under prefix paths like "copilot/..." -- a different backend,
     not Gemini, so those are eliminated here).
  2. It supports generateContent (the plain text-generation API the ADK
     Runner drives for /api/chat).
  3. It is a "flash" model -- flash is the default chat class (fast/cheap);
     pro-class ids are deliberately NOT auto-selected.
  4. It is not task-specialized (image/tts/translate/vision/thinking/agent/
     lite variants aren't general-purpose chat models).

BASE_URL TRAP (verified live against CLIProxyAPI with google-adk==1.36.2 on
2026-07-30): the ADK Gemini model object (google.adk.models.google_llm.
Gemini) must be built with base_url=<proxy root> and NO "/v1beta" suffix --
the genai SDK appends that path segment itself, so a suffixed base_url
double-encodes it and every request 404s. The "/v1beta" below exists ONLY
because fetch_models() is a RAW HTTP call to the catalog endpoint, which
does need the full path.

Auth needs no code here: the genai Client reads GOOGLE_API_KEY from the
environment and sends it as the x-goog-api-key header, which the proxy
accepts -- in production the GOOGLE_API_KEY secret simply holds the proxy
key (verified live: correct key 200, wrong/missing key 401).
"""

import json
import logging
import os
import re
import urllib.request

_HTTP_TIMEOUT_SECONDS = 10

# Task-specialized variants: not general-purpose text-chat models, even if
# they pass the gemini-/generateContent/flash filters. "lite" is included on
# purpose: flash-lite ids trade capability for cost, which is an operator
# choice, not something "always newest" should silently opt into.
_SPECIALIZED_MARKERS = ("image", "tts", "translate", "vision", "thinking", "agent", "lite")

# Variant preference among equal versions: "-high" > plain > "-low" >
# "-extra-low". "-extra-low" must be checked before "-low" because it also
# ends with "-low"; plain is the implicit middle slot (no suffix matches).
_VARIANT_SUFFIX_RANKS = (("-extra-low", 0), ("-low", 1), ("-high", 3))
_VARIANT_RANK_PLAIN = 2


def _strip_models_prefix(name: str) -> str:
    """Same shape as live_model: the catalog returns "models/<id>"."""
    if name.startswith("models/"):
        return name[len("models/") :]
    return name


def version_key(name: str) -> tuple:
    """Extract (major, minor) from a "gemini-<major>.<minor>..." model name
    for sort ordering -- the same regex pattern live_model.version_key uses.
    Unparseable input sorts lowest via (0, 0)."""
    model_name = _strip_models_prefix(name) if name else ""
    match = re.match(r"^gemini-(\d+)\.(\d+)", model_name)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def is_specialized(name: str) -> bool:
    """True iff `name` looks task-specialized (image/tts/translate/vision/
    thinking/agent/lite), i.e. not a general-purpose text-chat model."""
    lowered = name.lower()
    return any(marker in lowered for marker in _SPECIALIZED_MARKERS)


def _variant_rank(name: str) -> int:
    """Rank the capability variant suffix: higher wins on a version tie."""
    lowered = name.lower()
    for suffix, rank in _VARIANT_SUFFIX_RANKS:
        if lowered.endswith(suffix):
            return rank
    return _VARIANT_RANK_PLAIN


def fetch_models(base_url: str = "", api_key: str = "") -> list:
    """Real HTTP fetch: GET {base_url}/v1beta/models?pageSize=200 with the
    proxy key in the x-goog-api-key header.

    Both arguments default to the process environment (JARVIS_LLM_BASE_URL /
    GOOGLE_API_KEY) so resolve()'s default fetch works unwired, exactly like
    live_model.fetch_models() reads GOOGLE_API_KEY. Returns the raw list of
    model dicts from the response's "models" field (each shaped like
    {"name": "models/...", "supportedGenerationMethods": [...]}). Raises on
    network/HTTP/JSON failure -- callers (resolve(),
    config.resolve_text_model()) are responsible for falling back.
    """
    base_url = (base_url or os.environ.get("JARVIS_LLM_BASE_URL", "")).rstrip("/")
    api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
    url = f"{base_url}/v1beta/models?pageSize=200"
    request = urllib.request.Request(url, headers={"x-goog-api-key": api_key})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        body = response.read()
    data = json.loads(body)
    return data.get("models", [])


def resolve(fetch_models=fetch_models, fallback: str = "gemini-3.6-flash-high") -> str:
    """Pick the newest USABLE flash text-chat model, or `fallback` if none
    qualify.

    `fetch_models` is injectable for tests; it defaults to the real HTTP
    fetch (module-level fetch_models() above). Filters applied, in order:
    gemini- id -> generateContent support -> flash class -> not
    task-specialized. Among survivors the highest version_key() wins; ties
    prefer "-high" over plain over "-low" over "-extra-low", then fall back
    to lexical order.

    Logs a DATA-level line at INFO: how many models were fetched, how many
    survived the filters, and which one was ultimately chosen.
    """
    try:
        models = list(fetch_models())
    except Exception:
        logging.exception("text_model.resolve: fetch_models failed, using fallback %s", fallback)
        return fallback

    fetched_count = len(models)
    candidates = []
    for m in models:
        name = _strip_models_prefix(m.get("name", ""))
        if not name.startswith("gemini-"):
            continue
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        if "flash" not in name.lower():
            continue
        if is_specialized(name):
            continue
        candidates.append(name)

    passed_count = len(candidates)
    if not candidates:
        logging.info(
            "text_model.resolve: fetched=%d passed_filters=%d chosen=%s (fallback, no candidates)",
            fetched_count,
            passed_count,
            fallback,
        )
        return fallback

    def sort_key(name: str) -> tuple:
        # Higher version wins (max() picks the largest tuple); among equal
        # versions the higher variant rank wins ("-high" > plain > "-low" >
        # "-extra-low"); the final tiebreak is lexical, as in live_model.
        return (version_key(name), _variant_rank(name), name)

    chosen = max(candidates, key=sort_key)
    logging.info(
        "text_model.resolve: fetched=%d passed_filters=%d chosen=%s",
        fetched_count,
        passed_count,
        chosen,
    )
    return chosen
