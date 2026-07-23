import json

import pytest

from app import config
from app.voice_protocol import (
    AUDIO_IN_RATE, AUDIO_MIME_IN, AUDIO_OUT_RATE, evt_error, evt_transcript,
    evt_turn_complete, parse_hello,
)


def test_rates_fixed_by_contract():
    assert AUDIO_IN_RATE == 16000 and AUDIO_OUT_RATE == 24000
    assert AUDIO_MIME_IN == "audio/pcm;rate=16000"


def test_live_model_default_is_gemini_3_x_live():
    """Must be a Gemini 3.x Live model, NOT a "-latest" native-audio alias:
    confirmed via live smoke test (task-2a4-report.md) that ADK 1.36.2 buffers
    tool_call messages until turn_complete for any model where
    google.adk.utils.model_name_utils._is_gemini_3_x_live() is False -- which
    deadlocks forever, since turn_complete never arrives until the buffered
    (never-yielded) tool call gets a response."""
    from google.adk.utils import model_name_utils

    assert model_name_utils._is_gemini_3_x_live(config.LIVE_MODEL)


def test_events_shape():
    assert evt_transcript("user", "selam") == {"type": "transcript", "role": "user", "text": "selam"}
    assert evt_turn_complete() == {"type": "turn_complete"}
    assert evt_error("x")["type"] == "error"


def test_parse_hello_roundtrip_and_reject():
    assert parse_hello(json.dumps({"token": "abc"})) == "abc"
    with pytest.raises(ValueError):
        parse_hello("not json")
    with pytest.raises(ValueError):
        parse_hello(json.dumps({"no_token": 1}))
    with pytest.raises(ValueError):
        parse_hello("42")  # valid JSON, not an object
    with pytest.raises(ValueError):
        parse_hello(json.dumps([1, 2]))
