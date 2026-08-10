"""Tests for voice challenge-response and enrollment liveness verification (Phase C)."""
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from app import trust, voice_challenge as vc, voice_trust
from app.auth import require_google_user
import app.main as main_mod
from app.voice import APP_NAME, VoiceBridge, active_bridges
from tests.fakes import FakeDB


def test_digit_to_words_and_extract():
    assert vc.digit_to_words("4831") == "dört sekiz üç bir"
    assert vc.digit_to_words("0952") == "sıfır dokuz beş iki"

    assert vc.extract_digits_from_text("dört sekiz üç bir") == "4831"
    assert vc.extract_digits_from_text("Doğrulama kodum 4 8 3 1") == "4831"
    assert vc.extract_digits_from_text("Doğrulama kodu sıfır yedi sekiz altı") == "0786"


def test_create_challenge():
    db = FakeDB()
    now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
    code, doc = vc.create_challenge(db, "kadir@example.com", now_fn=lambda: now)

    assert len(code) == 4
    assert code.isdigit()
    assert doc["code"] == code
    assert doc["status"] == "pending"
    assert doc["created_at"] == now.isoformat()
    assert doc["expires_at"] == (now + timedelta(minutes=2)).isoformat()

    stored = db.collection("voice_challenges").document("kadir@example.com").get().to_dict()
    assert stored["code"] == code


def test_verify_and_grant_success():
    db = FakeDB()
    now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
    code, _ = vc.create_challenge(db, "kadir@example.com", now_fn=lambda: now)

    # Verify at t + 1 minute (within 2 min window)
    verify_time = now + timedelta(minutes=1)
    res = vc.verify_and_grant(db, "kadir@example.com", code, now_fn=lambda: verify_time)
    assert res is True

    stored = db.collection("voice_challenges").document("kadir@example.com").get().to_dict()
    assert stored["status"] == "granted"
    assert stored["expires_at"] == (verify_time + timedelta(minutes=5)).isoformat()

    # Check valid grant
    assert vc.has_valid_grant(db, "kadir@example.com", now_fn=lambda: verify_time) is True


def test_verify_and_grant_wrong_code():
    db = FakeDB()
    now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
    code, _ = vc.create_challenge(db, "kadir@example.com", now_fn=lambda: now)

    res = vc.verify_and_grant(db, "kadir@example.com", "9999", now_fn=lambda: now)
    assert res is False
    assert vc.has_valid_grant(db, "kadir@example.com", now_fn=lambda: now) is False


def test_verify_and_grant_expired():
    db = FakeDB()
    now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
    code, _ = vc.create_challenge(db, "kadir@example.com", now_fn=lambda: now)

    # 3 minutes later -> challenge expired (TTL is 2 mins)
    later = now + timedelta(minutes=3)
    res = vc.verify_and_grant(db, "kadir@example.com", code, now_fn=lambda: later)
    assert res is False
    assert vc.has_valid_grant(db, "kadir@example.com", now_fn=lambda: later) is False


def test_grant_expiration():
    db = FakeDB()
    now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
    code, _ = vc.create_challenge(db, "kadir@example.com", now_fn=lambda: now)
    vc.verify_and_grant(db, "kadir@example.com", code, now_fn=lambda: now)

    # 4 minutes later -> grant still valid (TTL 5 mins)
    t4 = now + timedelta(minutes=4)
    assert vc.has_valid_grant(db, "kadir@example.com", now_fn=lambda: t4) is True

    # 6 minutes later -> grant expired
    t6 = now + timedelta(minutes=6)
    assert vc.has_valid_grant(db, "kadir@example.com", now_fn=lambda: t6) is False


def test_corrupt_doc_fail_closed():
    db = FakeDB()
    db.collection("voice_challenges").document("kadir@example.com").set({"status": "invalid_data"})
    assert vc.has_valid_grant(db, "kadir@example.com") is False
    assert vc.verify_and_grant(db, "kadir@example.com", "1234") is False


def test_create_challenge_endpoint(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: db, raising=False)
    main_mod.app.dependency_overrides[require_google_user] = lambda: "kadir@example.com"

    try:
        with TestClient(main_mod.app) as c:
            r = c.post("/api/voice/challenge")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "challenge_created"
            assert data["code_spoken"] is False  # No active WS bridge

            stored = db.collection("voice_challenges").document("kadir@example.com").get().to_dict()
            assert stored["status"] == "pending"
            assert len(stored["code"]) == 4
    finally:
        main_mod.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_voice_bridge_challenge_interception():
    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            import json
            self.sent.append(json.loads(text))

    db = FakeDB()
    code, _ = vc.create_challenge(db, "kadir@example.com")

    bridge = VoiceBridge(runner=None, session_service=None)
    bridge._user_id = "kadir@example.com"
    bridge.memory = type("FakeMemory", (), {"db": db})()
    # The challenge grant now requires the CM to have cleared this utterance
    # (see test_antispoof.py's test_challenge_grant_* trio) -- without this,
    # cm_ok resolves to None (no signals published) and the grant is refused.
    bridge._trust_key = voice_trust.key_for(APP_NAME, "kadir@example.com", "voice-kadir@example.com")
    voice_trust.publish(bridge._trust_key, voice_trust.VoiceSignals(
        trust_level=trust.HIGH, voice_score=0.9, cm_ok=True,
    ))
    # It must also be FRESH (see test_antispoof.py's
    # test_challenge_grant_refuses_stale_cm_verdict, which pins the real
    # _verify_utterance wiring). This bridge has no speaker_service, so
    # _verify_utterance never runs -- this test's own subject is the WS
    # interception/reply mechanics, not the freshness logic, so the
    # precondition is seeded directly rather than routing fake PCM through a
    # full verification just to satisfy it.
    bridge._cm_verdict_fresh = True

    ws = FakeWS()
    bridge._ws = ws

    # Final utterance with matching digit words
    await bridge._on_utterance_final(ws, vc.digit_to_words(code))

    assert vc.has_valid_grant(db, "kadir@example.com") is True
    assert any(isinstance(m, dict) and m.get("text") == "Doğrulama kodu kabul edildi." for m in ws.sent)
