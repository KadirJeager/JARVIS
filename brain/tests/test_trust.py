from app.trust import TrustContext, assess, HIGH, MEDIUM, LOW

TH = 0.5  # accept threshold for tests

def test_no_auth_is_low():
    assert assess(TrustContext(auth_verified=False), TH) == LOW

def test_foreground_is_high_regardless_of_voice():
    assert assess(TrustContext(auth_verified=True, presence="foreground", voice_score=0.0), TH) == HIGH
    assert assess(TrustContext(auth_verified=True, presence="foreground", voice_score=None), TH) == HIGH

def test_text_path_no_voice_defaults_high_when_foreground():
    # voice_score None (text chat) + default foreground presence -> HIGH
    assert assess(TrustContext(auth_verified=True), TH) == HIGH

def test_locked_no_voice_is_medium():
    assert assess(TrustContext(auth_verified=True, presence="locked", voice_score=None), TH) == MEDIUM

def test_locked_voice_match_is_medium():
    assert assess(TrustContext(auth_verified=True, presence="locked", voice_score=0.9), TH) == MEDIUM

def test_locked_voice_mismatch_is_low():
    assert assess(TrustContext(auth_verified=True, presence="ambient", voice_score=0.1), TH) == LOW
