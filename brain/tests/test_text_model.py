"""Tests for app.text_model: auto-resolving the newest usable Gemini flash
text-chat model from a local CLIProxyAPI proxy catalog (and the
config.resolve_text_model / main._build_text_model wiring around it).

Fetch is ALWAYS injected -- no test here touches a real proxy.
"""

import logging

import pytest

from app import config, main, text_model


def _model(name, methods=("generateContent",)):
    """Build a synthetic entry shaped like the real
    GET {base_url}/v1beta/models response: {"name": "models/<id>",
    "supportedGenerationMethods": [...]}.
    """
    return {"name": f"models/{name}", "supportedGenerationMethods": list(methods)}


# ---------------------------------------------------------------------------
# version_key
# ---------------------------------------------------------------------------


def test_version_key_parses_major_minor():
    assert text_model.version_key("gemini-3.6-flash-high") == (3, 6)
    assert text_model.version_key("gemini-3.5-flash") == (3, 5)
    assert text_model.version_key("models/gemini-3.1-pro-preview") == (3, 1)


def test_version_key_unparseable_falls_back_to_zero_zero():
    assert text_model.version_key("not-a-gemini-model") == (0, 0)
    assert text_model.version_key("") == (0, 0)


# ---------------------------------------------------------------------------
# is_specialized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "gemini-3.1-flash-image",
        "gemini-3.5-flash-tts",
        "gemini-3.5-flash-translate",
        "gemini-3.5-flash-vision",
        "gemini-3.5-flash-thinking",
        "gemini-3-flash-agent",
        "gemini-pro-agent",
        "gemini-3.1-flash-lite",
    ],
)
def test_is_specialized_blocklist(name):
    assert text_model.is_specialized(name) is True


def test_is_specialized_false_for_general_purpose_flash():
    assert text_model.is_specialized("gemini-3.6-flash-high") is False
    assert text_model.is_specialized("gemini-3.5-flash") is False


# ---------------------------------------------------------------------------
# resolve() -- filters
# ---------------------------------------------------------------------------


def test_resolve_eliminates_copilot_prefixed_models():
    """The proxy also serves other providers under prefix paths; even a
    gemini-looking id behind such a prefix is a different backend."""
    models = [
        _model("copilot/gemini-9.9-flash"),
        _model("gemini-3.5-flash"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash"


def test_resolve_eliminates_specialized_variants():
    models = [
        _model("gemini-9.9-flash-image"),
        _model("gemini-9.8-flash-tts"),
        _model("gemini-9.7-flash-lite"),
        _model("gemini-9.6-flash-agent"),
        _model("gemini-3.5-flash"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash"


def test_resolve_eliminates_non_flash_models():
    """flash is the default chat class; pro-class ids are not auto-selected."""
    models = [
        _model("gemini-9.9-pro-preview"),
        _model("gemini-3.5-flash"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash"


def test_resolve_requires_generate_content_support():
    models = [
        _model("gemini-9.9-flash", methods=("countTokens",)),
        _model("gemini-3.5-flash"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash"


# ---------------------------------------------------------------------------
# resolve() -- ordering
# ---------------------------------------------------------------------------


def test_resolve_picks_newest_version():
    models = [
        _model("gemini-3.5-flash"),
        _model("gemini-3.6-flash-high"),
        _model("gemini-3-flash"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.6-flash-high"


def test_resolve_prefers_high_over_plain_over_low_over_extra_low():
    """Full variant ladder at the SAME version: -high > plain > -low >
    -extra-low."""
    models = [
        _model("gemini-3.5-flash-extra-low"),
        _model("gemini-3.5-flash-low"),
        _model("gemini-3.5-flash"),
        _model("gemini-3.5-flash-high"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash-high"


def test_resolve_plain_beats_low_when_no_high():
    models = [
        _model("gemini-3.5-flash-low"),
        _model("gemini-3.5-flash-extra-low"),
        _model("gemini-3.5-flash"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash"


def test_resolve_low_beats_extra_low_when_only_variants():
    models = [
        _model("gemini-3.5-flash-extra-low"),
        _model("gemini-3.5-flash-low"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.5-flash-low"


def test_resolve_newer_low_beats_older_high():
    """Version dominates variant preference: a newer -low still outranks an
    older -high."""
    models = [
        _model("gemini-3.5-flash-high"),
        _model("gemini-3.6-flash-low"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.6-flash-low"


# ---------------------------------------------------------------------------
# latency_first (the VOICE path)
# ---------------------------------------------------------------------------


def test_latency_first_prefers_fastest_tier():
    """Voice turns want the lowest-latency variant: extra-low > low > plain >
    high at the SAME version."""
    models = [
        _model("gemini-3.5-flash-high"),
        _model("gemini-3.5-flash"),
        _model("gemini-3.5-flash-low"),
        _model("gemini-3.5-flash-extra-low"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models, latency_first=True)
    assert chosen == "gemini-3.5-flash-extra-low"


def test_latency_first_version_still_dominates():
    """A newer slow variant still beats an older fast one: version first,
    variant second."""
    models = [
        _model("gemini-3.5-flash-extra-low"),
        _model("gemini-3.6-flash-high"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models, latency_first=True)
    assert chosen == "gemini-3.6-flash-high"


def test_resolve_voice_model_passes_latency_first(monkeypatch):
    """The config wrapper must actually reach for the fast tier, not just
    expose the flag: with only high+plain in the catalog the voice resolver
    picks plain, the text resolver picks high."""
    catalog = [_model("gemini-3.6-flash"), _model("gemini-3.6-flash-high")]
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://proxy.test")
    monkeypatch.delenv("JARVIS_VOICE_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_TEXT_MODEL", raising=False)
    monkeypatch.setattr(text_model, "fetch_models", lambda **kw: catalog)
    assert config.resolve_voice_model() == "gemini-3.6-flash"
    assert config.resolve_text_model() == "gemini-3.6-flash-high"


def test_resolve_voice_model_pins_and_fallbacks(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://proxy.test")
    monkeypatch.setenv("JARVIS_VOICE_MODEL", "gemini-pinned")
    assert config.resolve_voice_model() == "gemini-pinned"
    monkeypatch.delenv("JARVIS_VOICE_MODEL")

    def boom(**kw):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(text_model, "resolve", boom)
    assert config.resolve_voice_model() == config.VOICE_MODEL_FALLBACK


def test_build_text_model_voice_uses_voice_resolution(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "resolve_voice_model", lambda: "voice-model-id")
    monkeypatch.setattr(config, "resolve_text_model", lambda: "text-model-id")
    assert main._build_text_model(voice=True) == "voice-model-id"
    assert main._build_text_model() == "text-model-id"


# ---------------------------------------------------------------------------
# resolve() -- fallback paths
# ---------------------------------------------------------------------------


def test_resolve_falls_back_when_catalog_empty():
    chosen = text_model.resolve(fetch_models=lambda: [], fallback="gemini-3.6-flash-high")
    assert chosen == "gemini-3.6-flash-high"


def test_resolve_falls_back_when_nothing_passes_filters():
    models = [
        _model("copilot/gpt-5.2"),
        _model("gemini-3.1-flash-image"),
        _model("gemini-3.1-pro-preview"),
    ]
    chosen = text_model.resolve(fetch_models=lambda: models, fallback="gemini-3.6-flash-high")
    assert chosen == "gemini-3.6-flash-high"


def test_resolve_falls_back_when_fetch_raises():
    def boom():
        raise RuntimeError("proxy unreachable")

    chosen = text_model.resolve(fetch_models=boom, fallback="gemini-3.6-flash-high")
    assert chosen == "gemini-3.6-flash-high"


def test_resolve_logs_resolved_model_at_info(caplog):
    models = [_model("copilot/gpt-5.2"), _model("gemini-3.6-flash-high")]
    with caplog.at_level(logging.INFO, logger="root"):
        text_model.resolve(fetch_models=lambda: models)
    assert any(
        "fetched=2 passed_filters=1 chosen=gemini-3.6-flash-high" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# config.resolve_text_model()
# ---------------------------------------------------------------------------


def test_config_resolve_text_model_env_override_wins(monkeypatch):
    monkeypatch.setenv("JARVIS_TEXT_MODEL", "gemini-pinned-for-testing")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:8317")
    assert config.resolve_text_model() == "gemini-pinned-for-testing"


def test_config_resolve_text_model_returns_model_name_without_proxy(monkeypatch):
    """No proxy configured (LLM_BASE_URL empty) -> the direct AI Studio path
    keeps config.MODEL_NAME, unchanged pre-proxy behaviour."""
    monkeypatch.delenv("JARVIS_TEXT_MODEL", raising=False)
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    assert config.resolve_text_model() == config.MODEL_NAME


def test_config_resolve_text_model_delegates_to_text_model_resolve(monkeypatch):
    monkeypatch.delenv("JARVIS_TEXT_MODEL", raising=False)
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:8317")
    captured = {}

    def fake_resolve(fallback=None):
        captured["fallback"] = fallback
        return "gemini-9.9-flash-high"

    monkeypatch.setattr(text_model, "resolve", fake_resolve)
    assert config.resolve_text_model() == "gemini-9.9-flash-high"
    # config must forward its single-source fallback pin to resolve()
    assert captured["fallback"] == config.TEXT_MODEL_FALLBACK


def test_config_resolve_text_model_falls_back_when_resolve_raises(monkeypatch):
    monkeypatch.delenv("JARVIS_TEXT_MODEL", raising=False)
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:8317")

    def boom(*args, **kwargs):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(text_model, "resolve", boom)
    assert config.resolve_text_model() == config.TEXT_MODEL_FALLBACK


# ---------------------------------------------------------------------------
# main._build_text_model()
# ---------------------------------------------------------------------------


def test_build_text_model_returns_string_without_proxy(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "resolve_text_model", lambda: "gemini-flash-latest")
    assert main._build_text_model() == "gemini-flash-latest"


def test_build_text_model_returns_gemini_bound_to_proxy(monkeypatch):
    from google.adk.models.google_llm import Gemini

    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:8317")
    monkeypatch.setattr(config, "resolve_text_model", lambda: "gemini-3.6-flash-high")
    model = main._build_text_model()
    assert isinstance(model, Gemini)
    assert model.model == "gemini-3.6-flash-high"
    # NO "/v1beta" suffix -- the genai SDK appends it itself (see
    # app/text_model.py's module docstring for the 404 trap).
    assert model.base_url == "http://127.0.0.1:8317"
