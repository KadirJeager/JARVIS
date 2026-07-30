import json

import pytest

from app.voice_protocol import (
    AUDIO_IN_RATE, AUDIO_MIME_IN, evt_error, evt_jarvis_text, evt_speaker,
    evt_transcript, evt_turn_complete, parse_hello,
)


def test_rates_fixed_by_contract():
    """v2: only the INPUT rate survives -- mic PCM still flows in for
    speaker-ID. There is no server->client audio rate anymore: the server
    never sends binary frames (device TTS speaks jarvis_text locally)."""
    assert AUDIO_IN_RATE == 16000
    assert AUDIO_MIME_IN == "audio/pcm;rate=16000"


def test_events_shape():
    assert evt_transcript("user", "selam") == {"type": "transcript", "role": "user", "text": "selam"}
    assert evt_turn_complete() == {"type": "turn_complete"}
    assert evt_error("x")["type"] == "error"


def test_evt_jarvis_text_shape():
    """The v2 reply channel: the device TTS speaks exactly this text."""
    assert evt_jarvis_text("merhaba") == {"type": "jarvis_text", "text": "merhaba"}


def test_parse_hello_roundtrip_and_reject():
    assert parse_hello(json.dumps({"token": "abc"})) == {
        "token": "abc", "device_hint": "unknown", "presence": "foreground",
        "client_caps": None,
    }
    with pytest.raises(ValueError):
        parse_hello("not json")
    with pytest.raises(ValueError):
        parse_hello(json.dumps({"no_token": 1}))
    with pytest.raises(ValueError):
        parse_hello("42")  # valid JSON, not an object
    with pytest.raises(ValueError):
        parse_hello(json.dumps([1, 2]))


def test_parse_hello_full():
    h = parse_hello('{"token":"t","device_hint":"headset","presence":"locked",'
                    '"client_caps":{"stt":"device","tts":"device","proto":2}}')
    assert h == {
        "token": "t", "device_hint": "headset", "presence": "locked",
        "client_caps": {"stt": "device", "tts": "device", "proto": 2},
    }


def test_parse_hello_backward_compatible_defaults():
    """A caps-less hello still PARSES (v1 client) -- the 4409 rejection is the
    handshake's decision, not the parser's. client_caps surfaces as None."""
    h = parse_hello('{"token":"t"}')
    assert h == {
        "token": "t", "device_hint": "unknown", "presence": "foreground",
        "client_caps": None,
    }


def test_parse_hello_non_dict_client_caps_surfaces_as_none():
    """A malformed caps value must not crash the parser nor masquerade as a
    v2 client: treat it as absent."""
    h = parse_hello(json.dumps({"token": "t", "client_caps": "device"}))
    assert h["client_caps"] is None


def test_parse_hello_missing_token_raises():
    with pytest.raises(ValueError):
        parse_hello('{"device_hint":"phone"}')


def test_parse_hello_empty_token_raises():
    with pytest.raises(ValueError):
        parse_hello(json.dumps({"token": ""}))


def test_parse_hello_non_str_token_raises():
    with pytest.raises(ValueError):
        parse_hello(json.dumps({"token": 123}))


def test_evt_speaker_shape():
    assert evt_speaker("user", True, 0.87) == {
        "type": "speaker", "role": "user", "verified": True, "score": 0.87,
    }
