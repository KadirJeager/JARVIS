"""Permanent WebSocket voice contract, PROTOCOL v2 + v3.

v1 (frozen, gone): the client streamed mic PCM and the server bridged it to a
Gemini Live session, streaming model audio back. v2: STT and TTS moved onto
the device (client SpeechRecognizer/TTS; server runs TEXT turns through ADK
and never sends audio back). v3 (ses-v3 planı, 27 Ağu): STT/TTS moved to the
SERVER's Vertex legs -- the client streams PCM and plays back PCM; a
server-side VAD finds the utterance boundary, vertex_stt transcribes it, and
(from Task 3) vertex_tts speaks the reply as binary frames.

Client -> server: first TEXT frame is a hello JSON
{"token": "<google id token>", "client_caps": {"stt": ..., "tts": ...,
"proto": 2|3}, ...}. A hello WITHOUT client_caps is a v1 client: the server
answers evt_error("Uygulamayı güncelle") and closes with 4409. An unknown
proto gets the same 4409. After the hello:
  - BINARY frames are raw PCM16 mono 16kHz microphone audio. In v2 they feed
    ONLY speaker identification; in v3 they also feed the server VAD +
    transcriber.
  - TEXT frame {"type": "speech_start"}: (v2) the on-device STT detected
    speech onset. Lets the server trim the speaker-ID buffer to the utterance
    onset. v3 needs no onset frame: the server VAD applies the same trim.
  - TEXT frame {"type": "user_text", "text": ..., "utterance_final": bool}:
    (v2 ONLY) the on-device STT result; utterance_final=true is the utterance
    boundary. A v3 client NEVER sends this -- its transcripts arrive the other
    way (see evt_user_text below).
Server -> client: TEXT frames, JSON events built by the evt_* helpers below.
In v2 NO binary frames ever leave the server; in v3 BINARY frames are PCM16
mono 24kHz reply audio (Task 3).
"""
import json

AUDIO_IN_RATE = 16000
AUDIO_MIME_IN = "audio/pcm;rate=16000"


def evt_transcript(role: str, text: str) -> dict:
    return {"type": "transcript", "role": role, "text": text}


def evt_user_text(text: str) -> dict:
    """v3 server -> client: what the server-side STT heard, so the client can
    show the user's own utterance in the conversation UI (the v2 client knew
    it because ITS recognizer produced it; a v3 client never sees it unless
    the server reports it back)."""
    return {"type": "user_text", "text": text}


def evt_jarvis_text(text: str) -> dict:
    """The model's reply as TEXT: the client's on-device TTS speaks this."""
    return {"type": "jarvis_text", "text": text}


def evt_turn_complete() -> dict:
    return {"type": "turn_complete"}


def evt_error(message: str) -> dict:
    return {"type": "error", "message": message}


def evt_speaker(role: str, verified: bool, score: float, cm_ok: bool | None = None) -> dict:
    """Speaker verification result event.
    If `cm_ok` is None, the `cm_ok` key is omitted for backward compatibility.
    If `cm_ok` is bool (True/False), `"cm_ok": true/false` is included in the JSON object.
    """
    res = {"type": "speaker", "role": role, "verified": verified, "score": score}
    if cm_ok is not None:
        res["cm_ok"] = cm_ok
    return res


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
