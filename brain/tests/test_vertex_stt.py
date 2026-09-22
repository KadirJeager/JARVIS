"""Tests for app/vertex_stt.py — the protocol v3 cloud STT leg (Vertex Gemini
unary, WAV-wrapped PCM). The genai client is always faked; these pin the WAV
wrapping, the timeout/retry behaviour, and the error contract voice.py relies
on (VertexSttError -> evt_error, never a silent fallback)."""
import struct
import wave
import io

import pytest

from app import config, vertex_stt


def _pcm(seconds=1.0, amp=2000, rate=16000):
    n = int(seconds * rate)
    return struct.pack(f"<{n}h", *([amp] * n))


def _wav_headers(pcm, rate=16000):
    return vertex_stt.wrap_wav(pcm, rate)


def test_wrap_wav_produces_a_readable_16khz_mono_s16_file():
    pcm = _pcm(seconds=0.5)
    data = _wav_headers(pcm)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.readframes(10 ** 6) == pcm


def test_wrap_wav_honours_a_non_default_rate():
    data = vertex_stt.wrap_wav(_pcm(seconds=0.1), 8000)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getframerate() == 8000


class _FakeModels:
    """Duck-types genai client's .models.generate_content surface."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)   # each: str OR Exception instance
        self.calls = []

    def generate_content(self, *, model, contents):
        self.calls.append({"model": model, "contents": contents})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("R", (), {"text": outcome})()


class _FakeClient:
    def __init__(self, outcomes):
        self.models = _FakeModels(outcomes)


@pytest.fixture
def fake_client(monkeypatch):
    """Install a fake genai client factory; returns the holder tests read."""
    holder = {}

    def factory():
        return holder["client"]

    monkeypatch.setattr(vertex_stt, "_get_client", factory)
    return holder


@pytest.mark.asyncio
async def test_transcribe_returns_the_model_text(fake_client):
    fake_client["client"] = _FakeClient(["merhaba dünya"])
    text = await vertex_stt.transcribe(_pcm(seconds=1.0))
    assert text == "merhaba dünya"
    call = fake_client["client"].models.calls[0]
    assert call["model"] == config.VERTEX_STT_MODEL
    # The WAV part is in the request: prompt part first, audio part second.
    parts = call["contents"][0].parts
    assert parts[1].inline_data.mime_type == "audio/wav"


@pytest.mark.asyncio
async def test_transcribe_retries_once_then_raises_vertex_stt_error(fake_client):
    fake_client["client"] = _FakeClient([RuntimeError("boom"), RuntimeError("boom2")])
    with pytest.raises(vertex_stt.VertexSttError):
        await vertex_stt.transcribe(_pcm(seconds=1.0))
    assert len(fake_client["client"].models.calls) == 2


@pytest.mark.asyncio
async def test_transcribe_recovers_when_the_retry_succeeds(fake_client):
    fake_client["client"] = _FakeClient([RuntimeError("boom"), "ikinci deneme"])
    assert await vertex_stt.transcribe(_pcm(seconds=1.0)) == "ikinci deneme"


@pytest.mark.asyncio
async def test_an_empty_pcm_is_an_empty_transcript_not_an_api_call(fake_client):
    fake_client["client"] = _FakeClient(["asla gelmemeli"])
    assert await vertex_stt.transcribe(b"") == ""
    assert fake_client["client"].models.calls == []


@pytest.mark.asyncio
async def test_an_empty_model_answer_is_an_empty_string(fake_client):
    fake_client["client"] = _FakeClient([""])
    assert await vertex_stt.transcribe(_pcm(seconds=1.0)) == ""


@pytest.mark.asyncio
async def test_transcribe_runs_off_the_event_loop(fake_client):
    """The genai call is blocking sync I/O; on the loop it would stall every
    concurrent WS connection. Pin the to_thread hop."""
    import app.vertex_stt as mod
    seen = {}

    real_to_thread = __import__("asyncio").to_thread

    async def spy(fn, *a, **k):
        seen["called"] = True
        return await real_to_thread(fn, *a, **k)

    fake_client["client"] = _FakeClient(["x"])
    orig = mod.asyncio.to_thread
    mod.asyncio.to_thread = spy
    try:
        await vertex_stt.transcribe(_pcm(seconds=0.2))
    finally:
        mod.asyncio.to_thread = orig
    assert seen.get("called") is True
