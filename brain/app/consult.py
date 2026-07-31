"""Consultant-brain tools (North Star §4.9): the orchestrator can ask a SECOND
model family for a second opinion before committing to an answer or a plan.

Two tools are exposed: consult_gemini (a pro-class Gemini -- quality tier,
deliberately NOT the flash chat tier) and consult_claude (the newest Claude
the proxy serves). Both ride the SAME Gemini-compatible generateContent
endpoint the text-chat path uses: in production that is the CLIProxyAPI
sidecar (config.LLM_BASE_URL set), which fronts several providers behind one
/v1beta surface; locally, with LLM_BASE_URL empty, the tools go DIRECTLY to
AI Studio (https://generativelanguage.googleapis.com) -- the same wire
format, the same x-goog-api-key auth header, no extra configuration. The
alternative design (fail closed with "not configured" when no sidecar is
present) was rejected: it would make the tools dead in every dev/test setup
while adding no safety -- a failed call is already a model-visible
observation, not an exception.

DESIGN RULES carried over from text_model.py:
  - Model ids are NEVER hardcoded for resolution: they come from the live
    catalog (GET {base_url}/v1beta/models) so the tools follow the proxy's
    model upgrades automatically. The config fallbacks
    (CONSULT_GEMINI_FALLBACK / CONSULT_CLAUDE_FALLBACK) exist only for when
    the catalog cannot be fetched or holds nothing usable.
  - Resolution is cached per process per family: a tool call must NOT cost a
    catalog GET every time. Only SUCCESSFUL resolutions are cached -- a
    transient proxy outage pins the fallback for one call, not for the life
    of the process.
  - Errors are OBSERVATIONS (North Star principle 4): a failed consult
    returns a Turkish, information-carrying string ("Claude şu an cevap
    veremiyor: ...") to the calling model instead of raising. The orchestrator
    should be able to see that the consultant is unavailable and say so.

Auth needs no new code: GOOGLE_API_KEY is sent as the x-goog-api-key header,
exactly like text_model.fetch_models -- in production the secret simply holds
the proxy key.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.request

from . import config, text_model

# A consultant answer is a thinking-heavy, one-shot call, not a chat turn:
# 30 s (not text_model's 10 s catalog timeout) so a slow pro/opus response is
# not cut off mid-generation.
_HTTP_TIMEOUT_SECONDS = 30

# Consultant answers feed back into the orchestrator's context window; cap
# them so a verbose consultant cannot flood the conversation.
_MAX_RESPONSE_CHARS = 4000

# Direct AI Studio endpoint used when no sidecar is configured (see the
# module docstring for why this is the chosen empty-LLM_BASE_URL behaviour).
_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com"

_CONSULTANT_SYSTEM = (
    "Başka bir asistanın (Jarvis) sorusuna ikinci görüş veriyorsun. "
    "Kısa, somut ve Türkçe cevap ver; nihai karar Jarvis'e ait, sen danışmansın."
)

# Process-local resolution cache: family ("gemini"/"claude") -> model id.
# Tests clear this directly between cases.
_model_cache: dict[str, str] = {}

# Claude tier ladder: opus > sonnet > haiku. Consultation is a quality play,
# so the tier dominates recency WITHIN the family's own versioning.
_CLAUDE_CLASS_RANKS = (("opus", 3), ("sonnet", 2), ("haiku", 1))


def _base_url() -> str:
    """Effective endpoint root: the sidecar when configured, else AI Studio.
    Read from config at CALL time (not import time) so tests and deployments
    can rebind config.LLM_BASE_URL."""
    return (config.LLM_BASE_URL or _AI_STUDIO_BASE_URL).rstrip("/")


def _claude_sort_key(name: str) -> tuple:
    """(tier rank, version tuple, name) for max(): tier first (opus > sonnet
    > haiku), then the digit groups in the id as the version, then lexical."""
    lowered = name.lower()
    rank = 0
    for cls, cls_rank in _CLAUDE_CLASS_RANKS:
        if cls in lowered:
            rank = cls_rank
            break
    digits = tuple(int(d) for d in re.findall(r"\d+", name))
    return (rank, digits, name)


def resolve_consult_model(family: str, fetch_models=None) -> str:
    """Resolve the consultant model id for `family` ("gemini" or "claude")
    from the live catalog, with a per-process cache.

    family="gemini": newest PRO-class id -- contains "pro", supports
    generateContent, and is not task-specialized (text_model.is_specialized:
    image/tts/translate/vision/thinking/agent/lite). Flash ids are excluded
    on purpose: the orchestrator itself already runs on flash; a consultant
    that thinks the same way adds no signal.

    family="claude": newest "claude-" id with generateContent support, tier
    ladder opus > sonnet > haiku dominating the version digits.

    Ids behind a provider prefix ("copilot/...") never start with "gemini-"/
    "claude-" after the "models/" resource prefix is stripped, so they are
    eliminated by the same startswith filter as in text_model.

    `fetch_models` is injectable for tests (same pattern as
    text_model.resolve); the default is text_model.fetch_models bound to the
    effective base_url. Falls back to the config constant on ANY failure
    (fetch error, empty catalog, nothing usable); only successful resolutions
    are cached, so a transient outage does not pin the fallback forever.
    """
    if family in _model_cache:
        return _model_cache[family]

    if family == "gemini":
        fallback = config.CONSULT_GEMINI_FALLBACK
    else:
        fallback = config.CONSULT_CLAUDE_FALLBACK

    fetch = fetch_models or text_model.fetch_models
    try:
        models = list(fetch(base_url=_base_url()))
    except Exception:
        logging.exception(
            "consult.resolve_consult_model: catalog fetch failed for family=%s, using fallback %s",
            family, fallback,
        )
        return fallback

    candidates = []
    for m in models:
        name = text_model._strip_models_prefix(m.get("name", ""))
        if family == "gemini":
            if not name.startswith("gemini-") or "pro" not in name.lower():
                continue
        else:
            if not name.startswith("claude-"):
                continue
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if text_model.is_specialized(name):
            continue
        candidates.append(name)

    if not candidates:
        logging.info(
            "consult.resolve_consult_model: family=%s fetched=%d no usable candidate, fallback=%s",
            family, len(models), fallback,
        )
        return fallback

    if family == "gemini":
        chosen = max(candidates, key=lambda n: (text_model.version_key(n), n))
    else:
        chosen = max(candidates, key=_claude_sort_key)
    logging.info(
        "consult.resolve_consult_model: family=%s fetched=%d passed=%d chosen=%s",
        family, len(models), len(candidates), chosen,
    )
    _model_cache[family] = chosen
    return chosen


def _generate_content(model: str, question: str, context: str) -> str:
    """One raw POST {base_url}/v1beta/models/{model}:generateContent with the
    consultant system instruction, the optional context ahead of the question,
    and a hard 30 s timeout. Returns the concatenated candidate text ("" if
    the response carries none). Raises on network/HTTP failure -- _consult()
    turns that into a model-visible observation."""
    parts = []
    if context.strip():
        parts.append({"text": f"Bağlam:\n{context}"})
    parts.append({"text": question})
    body = json.dumps({
        "system_instruction": {"parts": [{"text": _CONSULTANT_SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{_base_url()}/v1beta/models/{model}:generateContent",
        data=body,
        headers={
            "x-goog-api-key": os.environ.get("GOOGLE_API_KEY", ""),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read())
    texts = [
        part["text"]
        for candidate in data.get("candidates", [])
        for part in candidate.get("content", {}).get("parts", [])
        if part.get("text")
    ]
    return "\n".join(texts)


def _consult(family: str, label: str, question: str, context: str) -> str:
    """Shared body of both tools: resolve, call, and -- on ANY failure --
    return a Turkish observation string instead of raising (North Star
    principle 4: an error is information the model should see, not an
    exception to swallow). HTTP status and the first 300 chars of the error
    body are logged so one failing call localizes the fault without a rerun."""
    try:
        model = resolve_consult_model(family)
        answer = _generate_content(model, question, context)
    except urllib.error.HTTPError as exc:
        try:
            snippet = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            snippet = ""
        logging.warning(
            "consult.%s: HTTP %s from consultant endpoint, body[:300]=%r",
            family, exc.code, snippet,
        )
        return f"{label} şu an cevap veremiyor: HTTP {exc.code} — {snippet or 'detay yok'}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # OSError covers socket.timeout; URLError covers connection-refused.
        logging.warning("consult.%s: consultant endpoint unreachable: %s", family, exc)
        return f"{label} şu an cevap veremiyor: bağlantı hatası ({exc})"
    except Exception:
        logging.exception("consult.%s: unexpected consultant failure", family)
        return f"{label} şu an cevap veremiyor: beklenmeyen bir hata oluştu"
    if not answer.strip():
        return f"{label} boş cevap döndürdü; soruyu biraz daha bağlamla tekrar denemek gerekebilir."
    return answer[:_MAX_RESPONSE_CHARS]


def consult_gemini(question: str, context: str = "") -> str:
    """Zor bir teknik soru, tasarım kararı veya emin olamadığın bir cevap için
    Gemini Pro sınıfı danışman modelden ikinci görüş ister. Soruyu `question`
    ile net sor; varsa ilgili bağlamı (kod parçası, hata mesajı, kendi ilk
    analizin) `context` alanına koy. Dönen cevabı nihai karar olarak değil,
    kendi değerlendirmenle karşılaştıracağın bir görüş olarak kullan."""
    return _consult("gemini", "Gemini", question, context)


def consult_claude(question: str, context: str = "") -> str:
    """Zor bir teknik soru, tasarım kararı veya emin olamadığın bir cevap için
    Claude danışman modelden ikinci görüş ister. Soruyu `question` ile net
    sor; varsa ilgili bağlamı (kod parçası, hata mesajı, kendi ilk analizin)
    `context` alanına koy. Dönen cevabı nihai karar olarak değil, kendi
    değerlendirmenle karşılaştıracağın bir görüş olarak kullan."""
    return _consult("claude", "Claude", question, context)
