"""Vertex Gemini unary STT — the protocol v3 cloud transcription leg.

Why unary and not a streaming STT: the 27 Aug spike measured the realistic
options (see docs/superpowers/plans/2026-08-27-ses-v3-f1-sse.md). The Chirp
family does not exist in this project in any location; gemini-3.5-transcribe
is not on Vertex yet; Speech V2 `latest_long` streams but transcribes at
classic quality ("wordeks"). Vertex `gemini-2.5-flash` transcribed 5.2 s of
Turkish speech in 2.72 s with clean punctuation — and its prompt carries the
term biasing list, replacing the on-device EXTRA_BIASING_STRINGS machinery
wholesale. The v2 protocol also only ever consumed FINAL transcripts
server-side, so losing partials is not a regression here.

The utterance boundary is voice.py's server-side VAD; this module only sees
complete utterances (PCM16 mono 16kHz), wraps them in a WAV header (Vertex
rejects raw audio/pcm inline_data) and asks the model for a verbatim
Turkish transcript.

Failure contract: ANY infrastructure failure (transport, quota, timeout,
second strike) raises VertexSttError. voice.py turns that into a user-visible
evt_error and NEVER falls back to an on-device path silently (plan Global
Constraints). An empty or unintelligible utterance is NOT an error: the model
answers "" per the prompt contract, and the caller just goes on listening.
"""
import asyncio
import io
import logging
import wave

from google import genai
from google.genai import types

from . import config


class VertexSttError(Exception):
    """The Vertex transcription could not be produced (after the retry)."""


_client = None


def _get_client():
    """Lazy singleton: credentials resolution (ADC/Workload Identity) is real
    I/O on first use, so it must not happen at import time (tests import every
    module). vertexai=True + explicit project/location — the same Cloud Run SA
    the rest of the brain's GCP access rides on."""
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=config.VERTEX_PROJECT,
            location=config.VERTEX_LOCATION,
        )
    return _client


def wrap_wav(pcm16: bytes, rate: int = 16000) -> bytes:
    """Wrap raw PCM16 mono samples in a minimal WAV container. Vertex accepts
    audio/wav inline_data but not headerless audio/pcm (spike, 27 Aug)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def _transcribe_sync(wav_bytes: bytes) -> str:
    """The blocking call, run off the event loop by transcribe(). One shot:
    prompt part first (instruction + term biasing), audio part second."""
    resp = _get_client().models.generate_content(
        model=config.VERTEX_STT_MODEL,
        contents=[types.Content(role="user", parts=[
            types.Part(text=config.VERTEX_STT_PROMPT),
            types.Part(inline_data=types.Blob(
                mime_type="audio/wav", data=wav_bytes)),
        ])],
    )
    return (resp.text or "").strip()


async def transcribe(pcm16: bytes, rate: int = 16000) -> str:
    """Transcribe one complete utterance (PCM16 mono). Returns "" for an empty
    buffer (no API call) and for an empty model answer. Retries ONCE on
    failure, then raises VertexSttError; the timeout applies per attempt.

    Off the event loop: genai's generate_content is blocking sync I/O, and
    the bridge's receive loop feeds every concurrent session — same reason
    speaker inference runs via asyncio.to_thread (voice.py)."""
    if not pcm16:
        return ""
    wav_bytes = wrap_wav(pcm16, rate)
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_transcribe_sync, wav_bytes),
                config.VERTEX_STT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — re-raised as VertexSttError below
            last_exc = exc
            logging.warning(
                "vertex_stt: attempt %d failed (%s: %s)%s",
                attempt, type(exc).__name__, exc,
                " -- retrying once" if attempt == 1 else "",
            )
    raise VertexSttError(f"transcription failed after 2 attempts: {last_exc}")
