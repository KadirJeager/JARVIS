"""Tests for app.live_model: auto-resolving the newest usable live model.

TDD note: written RED-first against a not-yet-existing app/live_model.py
(and app/config.resolve_live_model), per task-6-brief.md.
"""

import logging

import pytest

from app import config, live_model


def _model(name, methods=("bidiGenerateContent",)):
    """Build a synthetic entry shaped like the real
    GET /v1beta/models response: {"name": "models/<id>",
    "supportedGenerationMethods": [...]}.
    """
    return {"name": f"models/{name}", "supportedGenerationMethods": list(methods)}


# ---------------------------------------------------------------------------
# version_key
# ---------------------------------------------------------------------------


def test_version_key_parses_major_minor():
    assert live_model.version_key("gemini-3.1-flash-live-preview") == (3, 1)
    assert live_model.version_key("gemini-2.5-flash-native-audio-latest") == (2, 5)
    assert live_model.version_key("models/gemini-3.6-flash-live-preview") == (3, 6)


def test_version_key_unparseable_falls_back_to_zero_zero():
    assert live_model.version_key("not-a-gemini-model") == (0, 0)
    assert live_model.version_key("") == (0, 0)


def test_version_key_orders_higher_minor_above_lower():
    names = ["gemini-3.1-flash-live-preview", "gemini-3.6-flash-live-preview", "gemini-3.0-live"]
    ordered = sorted(names, key=live_model.version_key)
    assert ordered[-1] == "gemini-3.6-flash-live-preview"


# ---------------------------------------------------------------------------
# is_deadlock_safe (must match ADK's _is_gemini_3_x_live exactly)
# ---------------------------------------------------------------------------


def test_is_deadlock_safe_true_for_gemini_3_x_live():
    assert live_model.is_deadlock_safe("gemini-3.1-flash-live-preview") is True
    assert live_model.is_deadlock_safe("gemini-3.6-flash-live-preview") is True


def test_is_deadlock_safe_false_for_gemini_2_x_native_audio():
    assert live_model.is_deadlock_safe("gemini-2.5-flash-native-audio-latest") is False


def test_is_deadlock_safe_false_for_non_live_3_x():
    # 3.x but not a live model at all -- deadlock predicate is about the
    # live connection's tool-call flushing path, so a plain non-live model
    # doesn't qualify either.
    assert live_model.is_deadlock_safe("gemini-3.1-pro") is False


def test_is_deadlock_safe_matches_adk_predicate_when_importable():
    """If ADK's own predicate is importable, our chosen model for a resolve()
    call must pass IT too -- proves we didn't silently diverge from ADK."""
    try:
        from google.adk.utils.model_name_utils import _is_gemini_3_x_live
    except ImportError:
        pytest.skip("google.adk.utils.model_name_utils._is_gemini_3_x_live not importable")

    models = [
        _model("gemini-2.5-flash-native-audio-latest"),
        _model("gemini-3.1-flash-live-preview"),
        _model("gemini-3.6-flash-live-preview"),
    ]
    chosen = live_model.resolve(fetch_models=lambda: models)
    assert _is_gemini_3_x_live(chosen) is True


# ---------------------------------------------------------------------------
# is_specialized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "gemini-3.5-flash-translate-live",
        "gemini-3.5-flash-image-live",
        "gemini-3.5-flash-tts-live",
        "gemini-3.5-flash-vision-live",
        "gemini-3.5-flash-thinking-live",
    ],
)
def test_is_specialized_blocklist(name):
    assert live_model.is_specialized(name) is True


def test_is_specialized_false_for_general_purpose_model():
    assert live_model.is_specialized("gemini-3.1-flash-live-preview") is False


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


def test_resolve_picks_newest_live_general_purpose_model():
    """Synthetic list: an aliased 2.5 model, a real 3.1 live, a 3.5
    translate-specialized model, and a hypothetical newer 3.6 flash-live.
    The newest usable (3.6) must win over the older 3.1."""
    models = [
        _model("gemini-2.5-flash-native-audio-latest"),
        _model("gemini-3.1-flash-live-preview"),
        _model("gemini-3.5-flash-translate-live"),
        _model("gemini-3.6-flash-live-preview"),
    ]
    chosen = live_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.6-flash-live-preview"


def test_resolve_falls_back_when_nothing_passes_filters():
    models = [
        _model("gemini-2.5-flash-native-audio-latest"),
        _model("gemini-3.5-flash-translate-live"),
    ]
    chosen = live_model.resolve(fetch_models=lambda: models, fallback="gemini-3.1-flash-live-preview")
    assert chosen == "gemini-3.1-flash-live-preview"


def test_resolve_falls_back_when_fetch_returns_empty():
    chosen = live_model.resolve(fetch_models=lambda: [], fallback="gemini-3.1-flash-live-preview")
    assert chosen == "gemini-3.1-flash-live-preview"


def test_resolve_prefers_non_preview_name_on_version_tie():
    """Same (major, minor) version: the non-"preview" name must win the
    tie, per task-6-brief.md's "esitlikte preview olmayani yegle" rule."""
    models = [
        _model("gemini-3.1-flash-live-preview"),
        _model("gemini-3.1-flash-live"),
    ]
    chosen = live_model.resolve(fetch_models=lambda: models)
    assert chosen == "gemini-3.1-flash-live"


def test_resolve_ignores_models_without_bidi_support():
    models = [
        _model("gemini-3.9-flash-live-preview", methods=("generateContent",)),
        _model("gemini-3.1-flash-live-preview"),
    ]
    chosen = live_model.resolve(fetch_models=lambda: models, fallback="gemini-3.1-flash-live-preview")
    assert chosen == "gemini-3.1-flash-live-preview"


def test_resolve_logs_resolved_model_at_info(caplog):
    models = [_model("gemini-3.1-flash-live-preview")]
    with caplog.at_level(logging.INFO, logger="root"):
        live_model.resolve(fetch_models=lambda: models)
    assert any("gemini-3.1-flash-live-preview" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# config.resolve_live_model()
# ---------------------------------------------------------------------------


def test_config_resolve_live_model_env_override_wins(monkeypatch):
    monkeypatch.setenv("JARVIS_LIVE_MODEL", "gemini-pinned-for-testing")
    assert config.resolve_live_model() == "gemini-pinned-for-testing"


def test_config_resolve_live_model_falls_back_when_resolve_raises(monkeypatch):
    monkeypatch.delenv("JARVIS_LIVE_MODEL", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(live_model, "resolve", boom)
    result = config.resolve_live_model()
    assert result == "gemini-3.1-flash-live-preview"


def test_config_resolve_live_model_delegates_to_live_model_resolve(monkeypatch):
    monkeypatch.delenv("JARVIS_LIVE_MODEL", raising=False)
    captured = {}

    def fake_resolve(fallback=None):
        captured["fallback"] = fallback
        return "gemini-3.9-flash-live-preview"

    monkeypatch.setattr(live_model, "resolve", fake_resolve)
    assert config.resolve_live_model() == "gemini-3.9-flash-live-preview"
    # config must forward its single-source fallback pin to resolve()
    assert captured["fallback"] == config.LIVE_MODEL_FALLBACK
