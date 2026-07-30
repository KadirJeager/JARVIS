"""Permanent WebSocket voice contract, PROTOCOL v2 (device STT/TTS).

v1 (frozen, gone): the client streamed mic PCM and the server bridged it to a
Gemini Live session, streaming model audio back. v2: STT and TTS moved onto
the device. The client now runs SpeechRecognizer/TTS itself; the server runs
TEXT turns through ADK (run_async) and never sends audio back.

Client -> server: first TEXT frame is a hello JSON
{"token": "<google id token>", "client_caps": {"stt": "device", "tts":
"device", "proto": 2}, ...}. A hello WITHOUT client_caps is a v1 client: the
server answers evt_error("Uygulamayı güncelle") and closes with 4409 -- there
is no server-side audio path left to serve it. After the hello:
  - BINARY frames are raw PCM16 mono 16kHz microphone audio. They are used
    ONLY for speaker identification (app/voice.py's utterance buffer); no
    model ever transcribes them server-side.
  - TEXT frame {"type": "speech_start"}: the on-device STT detected speech
    onset (including barge-in). Lets the server trim the speaker-ID buffer
    to the utterance onset.
  - TEXT frame {"type": "user_text", "text": ..., "utterance_final": bool}:
    the on-device STT result. utterance_final=true is the utterance boundary:
    it triggers speaker verification and the model turn.
Server -> client: TEXT frames only, JSON events built by the evt_* helpers
below. NO binary frames ever leave the server in v2.
"""
import json

AUDIO_IN_RATE = 16000
AUDIO_MIME_IN = "audio/pcm;rate=16000"


def evt_transcript(role: str, text: str) -> dict:
    return {"type": "transcript", "role": role, "text": text}


def evt_jarvis_text(text: str) -> dict:
    """The model's reply as TEXT: the client's on-device TTS speaks this."""
    return {"type": "jarvis_text", "text": text}


def evt_turn_complete() -> dict:
    return {"type": "turn_complete"}


def evt_error(message: str) -> dict:
    return {"type": "error", "message": message}


def evt_speaker(role: str, verified: bool, score: float) -> dict:
    return {"type": "speaker", "role": role, "verified": verified, "score": score}


def parse_hello(raw: str) -> dict:
    """First TEXT frame: {"token", "device_hint"?, "presence"?, "client_caps"?}.
    device_hint and presence are optional (backward compatible with the
    token-only Katman 2a/2b hello); they feed the risk-based trust fusion
    (spec §6, §11). client_caps is the v2 marker: a dict like
    {"stt": "device", "tts": "device", "proto": 2} advertising that the
    client does its own STT/TTS. It is returned as-is (None when absent or
    not an object); the caller (voice._handshake) decides what absence means
    -- today: reject as a v1 client."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("hello frame is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("hello frame is not a JSON object")
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("hello frame missing token")
    caps = data.get("client_caps")
    return {
        "token": token,
        "device_hint": data.get("device_hint") or "unknown",
        "presence": data.get("presence") or "foreground",
        "client_caps": caps if isinstance(caps, dict) else None,
    }
