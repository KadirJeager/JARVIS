"""Tests for app.consult: the consultant-brain tools (North Star §4.9).

ALL HTTP is mocked -- no test here touches a real proxy or AI Studio. The
urllib layer is faked via monkeypatched urlopen; the catalog is either served
by that same fake or injected directly into resolve_consult_model.
"""

import io
import json
import urllib.error
import urllib.request

import pytest

from app import config, consult, policy


def _model(name, methods=("generateContent",)):
    """Synthetic catalog entry, shaped like GET {base_url}/v1beta/models."""
    return {"name": f"models/{name}", "supportedGenerationMethods": list(methods)}


class _FakeResponse:
    """Minimal urlopen return: bytes + context-manager protocol."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen(record, catalog=(), answer="Danışman cevabı."):
    """One fake for BOTH HTTP calls consult makes: a request with no body is
    the catalog GET, a request with a body is the generateContent POST."""
    def fake(request, timeout=None):
        record.append({
            "url": request.full_url,
            "method": request.get_method(),
            "headers": dict(request.header_items()),
            "data": request.data,
            "timeout": timeout,
        })
        if request.data is None:
            return _FakeResponse({"models": list(catalog)})
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": answer}]}}]})
    return fake


@pytest.fixture(autouse=True)
def _isolate_consult(monkeypatch):
    """The resolution cache is process-local by design; clear it around every
    test so one test's pick cannot leak into the next, and pin the proxy +
    key so the default is the sidecar path (the AI Studio path has its own
    dedicated test)."""
    consult._model_cache.clear()
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://proxy.test")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    yield
    consult._model_cache.clear()


# ---------------------------------------------------------------------------
# generateContent request shape
# ---------------------------------------------------------------------------


def test_generate_content_request_shape(monkeypatch):
    record = []
    catalog = [_model("gemini-9.9-pro")]
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(record, catalog))

    answer = consult.consult_gemini("BTree mi LSM mi?", context="Tablo 100M satır.")

    assert answer == "Danışman cevabı."
    post = next(r for r in record if r["data"] is not None)
    assert post["method"] == "POST"
    assert post["url"] == "http://proxy.test/v1beta/models/gemini-9.9-pro:generateContent"
    assert post["headers"]["X-goog-api-key"] == "test-key"
    assert post["timeout"] == 30
    body = json.loads(post["data"])
    assert "system_instruction" in body
    texts = [p["text"] for p in body["contents"][0]["parts"]]
    # Context rides AHEAD of the question (spec: context varsa önce o).
    assert texts == ["Bağlam:\nTablo 100M satır.", "BTree mi LSM mi?"]


def test_generate_content_omits_empty_context(monkeypatch):
    record = []
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen(record, [_model("claude-opus-4-6")])
    )
    consult.consult_claude("Sadece soru.")
    post = next(r for r in record if r["data"] is not None)
    texts = [p["text"] for p in json.loads(post["data"])["contents"][0]["parts"]]
    assert texts == ["Sadece soru."]


def test_empty_base_url_goes_direct_to_ai_studio(monkeypatch):
    """Design pin: LLM_BASE_URL empty does NOT mean "unconfigured" -- the
    tools talk to AI Studio directly, same genai-compatible endpoint."""
    record = []
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen(record, [_model("gemini-3.1-pro-preview")])
    )
    consult.consult_gemini("Soru.")
    assert all(
        r["url"].startswith("https://generativelanguage.googleapis.com/") for r in record
    )


# ---------------------------------------------------------------------------
# resolve_consult_model -- gemini family
# ---------------------------------------------------------------------------


def test_gemini_picks_pro_and_eliminates_flash():
    """A NEWER flash must still lose to an older pro: the orchestrator already
    runs on flash; the consultant exists for the quality tier."""
    catalog = [
        _model("gemini-9.9-flash-high"),
        _model("gemini-9.8-flash"),
        _model("gemini-3.1-pro-preview"),
    ]
    chosen = consult.resolve_consult_model("gemini", fetch_models=lambda **kw: catalog)
    assert chosen == "gemini-3.1-pro-preview"


def test_gemini_picks_newest_pro():
    catalog = [_model("gemini-3.1-pro-preview"), _model("gemini-3.5-pro")]
    chosen = consult.resolve_consult_model("gemini", fetch_models=lambda **kw: catalog)
    assert chosen == "gemini-3.5-pro"


def test_gemini_eliminates_specialized_pro_variants():
    catalog = [
        _model("gemini-9.9-pro-image"),
        _model("gemini-9.8-pro-thinking"),
        _model("gemini-3.1-pro-preview"),
    ]
    chosen = consult.resolve_consult_model("gemini", fetch_models=lambda **kw: catalog)
    assert chosen == "gemini-3.1-pro-preview"


# ---------------------------------------------------------------------------
# resolve_consult_model -- claude family
# ---------------------------------------------------------------------------


def test_claude_tier_ladder_opus_beats_sonnet_beats_haiku():
    """Tier dominates version digits: an older opus still outranks a newer
    sonnet -- consultation is a quality play."""
    catalog = [
        _model("claude-haiku-9"),
        _model("claude-sonnet-5"),
        _model("claude-opus-4-6"),
    ]
    chosen = consult.resolve_consult_model("claude", fetch_models=lambda **kw: catalog)
    assert chosen == "claude-opus-4-6"


def test_claude_newest_within_same_tier():
    catalog = [_model("claude-sonnet-4"), _model("claude-sonnet-5")]
    chosen = consult.resolve_consult_model("claude", fetch_models=lambda **kw: catalog)
    assert chosen == "claude-sonnet-5"


def test_claude_falls_to_sonnet_when_no_opus():
    catalog = [_model("claude-haiku-3"), _model("claude-sonnet-5")]
    chosen = consult.resolve_consult_model("claude", fetch_models=lambda **kw: catalog)
    assert chosen == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# resolve_consult_model -- prefix elimination, fallback, cache
# ---------------------------------------------------------------------------


def test_copilot_prefixed_models_are_eliminated():
    """The proxy serves other providers under prefix paths; even a matching
    id behind such a prefix is a different backend."""
    gemini_catalog = [_model("copilot/gemini-9.9-pro"), _model("gemini-3.1-pro-preview")]
    assert consult.resolve_consult_model(
        "gemini", fetch_models=lambda **kw: gemini_catalog
    ) == "gemini-3.1-pro-preview"
    claude_catalog = [_model("copilot/claude-opus-9"), _model("claude-sonnet-5")]
    assert consult.resolve_consult_model(
        "claude", fetch_models=lambda **kw: claude_catalog
    ) == "claude-sonnet-5"


def test_empty_catalog_falls_back_to_config_constants():
    assert consult.resolve_consult_model("gemini", fetch_models=lambda **kw: []) == \
        config.CONSULT_GEMINI_FALLBACK
    assert consult.resolve_consult_model("claude", fetch_models=lambda **kw: []) == \
        config.CONSULT_CLAUDE_FALLBACK


def test_fetch_failure_falls_back_and_is_not_cached():
    """A transient outage must not pin the fallback for the process: the
    failure is NOT cached, so a later call retries the catalog."""
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("proxy down")
        return [_model("gemini-3.5-pro")]

    assert consult.resolve_consult_model("gemini", fetch_models=flaky) == \
        config.CONSULT_GEMINI_FALLBACK
    assert consult.resolve_consult_model("gemini", fetch_models=flaky) == "gemini-3.5-pro"
    assert calls["n"] == 2


def test_successful_resolution_is_cached():
    """The second call for the same family must NOT cost a catalog GET."""
    calls = {"n": 0}

    def counting(**kw):
        calls["n"] += 1
        return [_model("claude-opus-4-6")]

    first = consult.resolve_consult_model("claude", fetch_models=counting)
    second = consult.resolve_consult_model("claude", fetch_models=counting)
    assert first == second == "claude-opus-4-6"
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Error = observation (North Star principle 4)
# ---------------------------------------------------------------------------


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://proxy.test/v1beta/models/x:generateContent", code, "err", {},
        io.BytesIO(body.encode("utf-8")),
    )


def test_http_429_returns_turkish_observation(monkeypatch):
    monkeypatch.setattr(
        consult, "resolve_consult_model", lambda family: "claude-opus-4-6"
    )

    def quota_hit(request, timeout=None):
        raise _http_error(429, '{"error": "quota dolu, yarın dene"}')

    monkeypatch.setattr(urllib.request, "urlopen", quota_hit)
    result = consult.consult_claude("Soru.")
    assert result.startswith("Claude şu an cevap veremiyor")
    assert "429" in result
    assert "quota dolu" in result


def test_timeout_returns_turkish_observation(monkeypatch):
    monkeypatch.setattr(
        consult, "resolve_consult_model", lambda family: "gemini-3.5-pro"
    )

    def slow(request, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", slow)
    result = consult.consult_gemini("Soru.")
    assert result.startswith("Gemini şu an cevap veremiyor")
    assert "timed out" in result


def test_unexpected_error_returns_observation_not_exception(monkeypatch):
    monkeypatch.setattr(
        consult, "resolve_consult_model", lambda family: "gemini-3.5-pro"
    )
    monkeypatch.setattr(
        consult, "_generate_content",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("bozuk yanıt")),
    )
    result = consult.consult_gemini("Soru.")
    assert result.startswith("Gemini şu an cevap veremiyor")


# ---------------------------------------------------------------------------
# Output cap
# ---------------------------------------------------------------------------


def test_answer_is_capped_at_4000_chars(monkeypatch):
    monkeypatch.setattr(consult, "resolve_consult_model", lambda family: "claude-opus-4-6")
    monkeypatch.setattr(consult, "_generate_content", lambda *a, **kw: "x" * 5000)
    result = consult.consult_claude("Soru.")
    assert len(result) == 4000


# ---------------------------------------------------------------------------
# Wiring: zones + tool registration
# ---------------------------------------------------------------------------


def test_consult_gemini_is_yellow_zone():
    """§9: misafir danışma yeşil-sarı arası -> SARI (yap + bildir)."""
    assert policy.check_zone("consult_gemini") == "yellow"


def test_consult_gemini_registered_in_all_tools():
    from tests.fakes import wired_into_all_tools
    assert wired_into_all_tools(consult.consult_gemini)


def test_consult_claude_is_removed_from_the_agent_surface():
    """KALDIRILDI (Kadir, 4 Ağu 02:48): "o öyle bi şey değildi — telefon
    terminalinden veya uzak PC'den CLI üzerinden danışmaydı kurgusu." Ajanın
    araç setine hiç ait değildi; ölü OAuth token'ı da sidecar'ı 5 dakikada bir
    invalid_grant'la söyletiyordu. Fonksiyon module'de duruyor (CLI kurgusu
    geri gelirse yeri hazır) ama KAYITLI DEĞİL ve bölge tablosunda da yok —
    bilinmeyen araç fail-closed KIRMIZIDIR (check_zone'un varsayılanı).

    ÖLDÜREN MUTASYON: consult_claude'u ALL_TOOLS'a ya da TOOL_ZONES'a geri
    eklemek."""
    from tests.fakes import wired_into_all_tools
    assert not wired_into_all_tools(consult.consult_claude)
    assert policy.check_zone("consult_claude") == "red"
