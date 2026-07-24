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


def evt_speaker(role: str, verified: bool, score: float) -> dict:
    return {"type": "speaker", "role": role, "verified": verified, "score": score}


def parse_hello(raw: str) -> dict:
    """First TEXT frame: {"token", "device_hint"?, "presence"?}. device_hint and
    presence are optional (backward compatible with the token-only Katman 2a/2b
    hello); they feed the risk-based trust fusion (spec §6, §11)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("hello frame is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("hello frame is not a JSON object")
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("hello frame missing token")
    return {
        "token": token,
        "device_hint": data.get("device_hint") or "unknown",
        "presence": data.get("presence") or "foreground",
    }
