"""Permanent WebSocket voice contract (Katman 2b Android reuses this verbatim).

Client -> server: first TEXT frame is a hello JSON {"token": "<google id token>"};
after auth, BINARY frames are raw PCM16 mono 16kHz microphone audio.
Server -> client: BINARY frames are raw PCM16 mono 24kHz model audio;
TEXT frames are JSON events built by the evt_* helpers below.
"""
import json

AUDIO_IN_RATE = 16000
AUDIO_OUT_RATE = 24000
AUDIO_MIME_IN = "audio/pcm;rate=16000"


def evt_transcript(role: str, text: str) -> dict:
    return {"type": "transcript", "role": role, "text": text}


def evt_turn_complete() -> dict:
    return {"type": "turn_complete"}


def evt_error(message: str) -> dict:
    return {"type": "error", "message": message}


def parse_hello(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("hello frame is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("hello frame is not a JSON object")
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("hello frame missing token")
    return token
