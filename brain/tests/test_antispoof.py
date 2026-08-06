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
