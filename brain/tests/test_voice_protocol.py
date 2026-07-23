import json

import pytest

from app import config
from app.voice_protocol import (
    AUDIO_IN_RATE, AUDIO_OUT_RATE, evt_error, evt_transcript,
    evt_turn_complete, parse_hello,
)


def test_rates_fixed_by_contract():
    assert AUDIO_IN_RATE == 16000 and AUDIO_OUT_RATE == 24000


def test_live_model_default_uses_latest_alias():
    assert "latest" in config.LIVE_MODEL


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
