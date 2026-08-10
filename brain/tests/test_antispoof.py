"""Unit tests for Phase B Anti-Spoofing (CM) gate in app/antispoof.py and voice pipeline.

Validates:
1. Module importability without torch.
2. `is_bonafide` threshold behavior and test injection mechanism (`_score_fn`).
3. Exception propagation on failure.
4. Voice pipeline integration (CM spoof reject, CM error fail-safe, CM bonafide adaptation).
5. `evt_speaker` event payload backwards compatibility.
"""
import asyncio
import pytest

from app import antispoof, config, trust, voice_protocol as vp, voice_trust
from app.speaker import IdentifyOutcome, SpeakerService
from app.voice import APP_NAME, VoiceBridge, _handshake
from tests.fakes import FakeDB


@pytest.fixture(autouse=True)
def _clean_score_fn():
    """Ensure _score_fn is cleared after each test."""
    yield
    antispoof._score_fn = None


def test_antispoof_importable_without_torch():
    """Verify antispoof module can be imported and accessed without torch."""
    assert callable(antispoof.is_bonafide)
    assert antispoof._model is None


def test_is_bonafide_threshold_behavior():
    """Verify threshold boundary: cm_fake_prob < 0.85 -> bonafide (True), >= 0.85 -> spoof (False)."""
    # 1. Below threshold (0.84) -> Bonafide
    antispoof._score_fn = lambda pcm: 0.84
    is_bonafide, fake_prob = antispoof.is_bonafide(b"1234")
    assert is_bonafide is True
    assert fake_prob == 0.84

    # 2. Exactly at threshold (0.85) -> Red (False)
    antispoof._score_fn = lambda pcm: 0.85
    is_bonafide, fake_prob = antispoof.is_bonafide(b"1234")
    assert is_bonafide is False
    assert fake_prob == 0.85

    # 3. High synthetic probability (0.99) -> Red (False)
    antispoof._score_fn = lambda pcm: 0.99
    is_bonafide, fake_prob = antispoof.is_bonafide(b"1234")
    assert is_bonafide is False
    assert fake_prob == 0.99


def test_is_bonafide_custom_tuple_return():
    """Verify _score_fn can return explicit (bool, float) tuple."""
    antispoof._score_fn = lambda pcm: (False, 0.95)
    is_bonafide, fake_prob = antispoof.is_bonafide(b"1234")
    assert is_bonafide is False
    assert fake_prob == 0.95


def test_is_bonafide_exception_propagation():
    """Verify is_bonafide raises exception when score function / model fails."""
    def exploding_score(pcm):
        raise RuntimeError("Model loading failed")

    antispoof._score_fn = exploding_score
    with pytest.raises(RuntimeError, match="Model loading failed"):
        antispoof.is_bonafide(b"1234")


def test_evt_speaker_backwards_compatibility():
    """Verify evt_speaker omits cm_ok key when None, and includes cm_ok when bool."""
    # None -> no cm_ok key in JSON dict
    event_none = vp.evt_speaker("user", True, 0.9, cm_ok=None)
    assert event_none == {"type": "speaker", "role": "user", "verified": True, "score": 0.9}
    assert "cm_ok" not in event_none

    # True -> cm_ok: True
    event_true = vp.evt_speaker("user", True, 0.9, cm_ok=True)
    assert event_true == {"type": "speaker", "role": "user", "verified": True, "score": 0.9, "cm_ok": True}

    # False -> cm_ok: False
    event_false = vp.evt_speaker("user", False, 0.2, cm_ok=False)
    assert event_false == {"type": "speaker", "role": "user", "verified": False, "score": 0.2, "cm_ok": False}


def test_speaker_identify_cm_ok_adaptation_gate():
    """Verify SpeakerService.identify only adapts when cm_ok is True."""
    A = [1.0, 0.0, 0.0]
    db = FakeDB()
    from app.speaker_store import enroll_anchors, load_profile
    enroll_anchors(db, "k", [A])
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t", accept=0.35, adapt=0.6)

    # 1. cm_ok is False (spoof red) -> score is verified, but NO adaptation
    out_red = svc.identify("k", b"pcm", "phone", auth_is_kadir=True, cm_ok=False)
    assert out_red.verified is True
    assert out_red.adapted_sample_id is None
    assert len(load_profile(db, "k").adaptive) == 0

    # 2. cm_ok is None (CM error/unavailable) -> score is verified, but NO adaptation
    out_none = svc.identify("k", b"pcm", "phone", auth_is_kadir=True, cm_ok=None)
    assert out_none.verified is True
    assert out_none.adapted_sample_id is None
    assert len(load_profile(db, "k").adaptive) == 0

    # 3. cm_ok is True (bonafide) -> score is verified AND adaptation lands
    out_true = svc.identify("k", b"pcm", "phone", auth_is_kadir=True, cm_ok=True)
    assert out_true.verified is True
    assert out_true.adapted_sample_id is not None
    assert len(load_profile(db, "k").adaptive) == 1


class _RecordingSpeaker:
    def __init__(self):
        self.calls = []
        self.history = []

    def identify(self, user_id, pcm, device_hint, auth_is_kadir, allow_adapt=True, cm_ok=None):
        self.calls.append((user_id, pcm, device_hint, auth_is_kadir, allow_adapt, cm_ok))
        return IdentifyOutcome(verified=True, score=0.9, vec=[1.0, 0.0], adapted_sample_id=None)

    def record_history(self, user_id, **entry):
        self.history.append((user_id, entry))
        return "h1"


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


@pytest.mark.asyncio
async def test_voice_verify_utterance_cm_spoof_red(caplog):
    """Verify CM spoof reject: logs warning, passes cm_ok=False to identify and evt_speaker."""
    antispoof._score_fn = lambda pcm: (False, 0.99)  # Spoof!

    speaker = _RecordingSpeaker()
    bridge = VoiceBridge(runner=None, session_service=None, speaker_service=speaker, presence="locked", device_hint="phone")
    bridge._trust_key = voice_trust.key_for(APP_NAME, "user1", "voice-user1")
    bridge._user_id = "user1"
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    ws = _FakeWS()

    with caplog.at_level("WARNING"):
        await bridge._verify_utterance(ws, min_bytes=0)

    assert "voice CM: SPOOF REDDI" in caplog.text
    assert len(speaker.calls) == 1
    # identify received cm_ok=False
    assert speaker.calls[0][5] is False

    # Trust signals published cm_ok=False
    signals = voice_trust.peek(bridge._trust_key)
    assert signals.cm_ok is False

    # Event sent with cm_ok: false
    import json
    sent_events = [json.loads(s) for s in ws.sent]
    assert sent_events[0] == {"type": "speaker", "role": "user", "verified": True, "score": 0.9, "cm_ok": False}

    # History entry includes cm_fake_prob
    assert len(speaker.history) == 1
    assert speaker.history[0][1]["cm_fake_prob"] == 0.99


@pytest.mark.asyncio
async def test_voice_verify_utterance_cm_exception_fail_safe(caplog):
    """Verify CM failure/timeout: logs exception, falls back to cm_ok=None, stream continues."""
    def exploding_cm(pcm):
        raise RuntimeError("CM model timeout")

    antispoof._score_fn = exploding_cm

    speaker = _RecordingSpeaker()
    bridge = VoiceBridge(runner=None, session_service=None, speaker_service=speaker, presence="locked", device_hint="phone")
    bridge._trust_key = voice_trust.key_for(APP_NAME, "user1", "voice-user1")
    bridge._user_id = "user1"
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    ws = _FakeWS()

    with caplog.at_level("ERROR"):
        await bridge._verify_utterance(ws, min_bytes=0)

    assert "voice bridge: CM evaluation failed" in caplog.text
    assert len(speaker.calls) == 1
    # identify received cm_ok=None
    assert speaker.calls[0][5] is None

    # Trust signals published cm_ok=None
    signals = voice_trust.peek(bridge._trust_key)
    assert signals.cm_ok is None

    # Event sent WITHOUT cm_ok key
    import json
    sent_events = [json.loads(s) for s in ws.sent]
    assert sent_events[0] == {"type": "speaker", "role": "user", "verified": True, "score": 0.9}
    assert "cm_ok" not in sent_events[0]


# --- Inference cost control (prod evidence 2026-08-10: CM timed out 3/3 on a
# 1 vCPU Cloud Run instance while the 6 Aug benchmark measured 0.46 s for 3 s of
# audio AT 2 THREADS. Two unbounded inputs feed that gap: torch's thread count
# (it sizes to host cores, not the cgroup CPU quota) and the utterance length
# (wav2vec2 self-attention grows superlinearly). Both are pure functions here so
# they stay testable without torch.) ---

_RATE = 16000  # Hz, PCM16 mono -> 2 bytes per sample


def test_bounded_window_returns_short_audio_untouched():
    """Audio at or below the cap is passed through byte-identical."""
    pcm = b"\x01\x02" * (_RATE * 2)  # 2.0 s
    assert antispoof._bounded_window(pcm, 4.0, _RATE) is pcm


def test_bounded_window_trims_long_audio_to_cap():
    """Audio above the cap is trimmed to exactly cap seconds of samples."""
    pcm = b"\x01\x02" * (_RATE * 10)  # 10.0 s
    out = antispoof._bounded_window(pcm, 4.0, _RATE)
    assert len(out) == int(4.0 * _RATE) * 2


def test_bounded_window_is_centered():
    """The kept window is centered: leading silence and a clipped tail both fall
    outside it. Marker bytes at the midpoint must survive."""
    samples = _RATE * 10
    buf = bytearray(b"\x00\x00" * samples)
    mid = (samples // 2) * 2
    buf[mid:mid + 2] = b"\x7f\x7f"
    out = antispoof._bounded_window(bytes(buf), 4.0, _RATE)
    assert b"\x7f\x7f" in out


def test_bounded_window_keeps_sample_alignment():
    """A window must never start on an odd byte: PCM16 samples are 2 bytes, and a
    1-byte shift reinterprets the whole stream as noise."""
    pcm = b"\x01\x02" * (_RATE * 10) + b"\x03"  # odd total length
    out = antispoof._bounded_window(pcm, 3.0, _RATE)
    assert len(out) % 2 == 0
    start = pcm.find(out[:64])
    assert start % 2 == 0


def test_resolve_thread_count_env_override_wins():
    """An explicit env value is honoured verbatim -- the knob must be turnable in
    prod without a rebuild."""
    assert antispoof._resolve_thread_count(env="3", quota_reader=lambda: 1.0) == 3


def test_resolve_thread_count_follows_cgroup_quota():
    """Absent an override, the thread count follows the container's CPU quota --
    NOT the host core count, which is what torch would pick on its own."""
    assert antispoof._resolve_thread_count(env=None, quota_reader=lambda: 1.0) == 1
    assert antispoof._resolve_thread_count(env=None, quota_reader=lambda: 2.0) == 2


def test_resolve_thread_count_clamps_high_quota():
    """Clamped: past a handful of threads this model stops scaling and the extra
    threads only add contention."""
    assert antispoof._resolve_thread_count(env=None, quota_reader=lambda: 64.0) == 4


def test_resolve_thread_count_falls_back_when_quota_unreadable():
    """An unreadable quota must not raise and must not fall back to 'unbounded'."""
    def _boom():
        raise OSError("no cgroup here")

    assert antispoof._resolve_thread_count(env=None, quota_reader=_boom) == 2


def test_resolve_thread_count_rejects_garbage_env():
    """A malformed override falls back to the quota path rather than crashing the
    voice turn."""
    assert antispoof._resolve_thread_count(env="abc", quota_reader=lambda: 1.0) == 1
    assert antispoof._resolve_thread_count(env="0", quota_reader=lambda: 1.0) == 1


# --- Startup warmup gating. The same image serves jarvis-brain and jarvis-voice;
# only the latter scores audio, and the CM model's ~1.2 GiB does not fit the
# brain's container. ---

def test_warmup_targets_exclude_cm_by_default(monkeypatch):
    """Default OFF: the brain must not pre-load a model it never calls."""
    from app import main

    monkeypatch.setattr(config, "CM_WARMUP", False)
    monkeypatch.setattr(config, "CM_ENABLED", True)
    assert [n for n, _ in main._warmup_targets()] == ["e5", "ecapa"]


def test_warmup_targets_include_cm_when_opted_in(monkeypatch):
    """Opted in (the voice service): CM joins so the first utterance after a cold
    start does not spend its whole budget loading weights."""
    from app import main

    monkeypatch.setattr(config, "CM_WARMUP", True)
    monkeypatch.setattr(config, "CM_ENABLED", True)
    assert [n for n, _ in main._warmup_targets()] == ["e5", "ecapa", "cm"]


def test_warmup_targets_respect_cm_disabled(monkeypatch):
    """CM switched off wins over the warmup opt-in -- warming a gate that will
    never run is pure waste."""
    from app import main

    monkeypatch.setattr(config, "CM_WARMUP", True)
    monkeypatch.setattr(config, "CM_ENABLED", False)
    assert [n for n, _ in main._warmup_targets()] == ["e5", "ecapa"]
