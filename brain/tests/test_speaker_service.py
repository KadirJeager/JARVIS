from app.speaker import SpeakerService
from app.speaker_store import enroll_anchors, load_profile
from tests.fakes import FakeDB

A = [1.0, 0.0, 0.0]
# Cosine similarity is angle-only (scale-invariant), so a "small secondary
# component" like [0.98, 0.02, 0.0] is NOT a small cosine deviation from A --
# its cosine to A is ~0.9998 (well above adapt=0.97), not the intended
# accept<=score<adapt band. NEAR_A is instead constructed as a unit vector at
# angle arccos(0.95) from A, giving an EXACT cosine of 0.95 -- verified via
# cos(A, NEAR_A) == 0.95 (dot=0.95, both vectors unit-norm by construction).
NEAR_A = [0.95, 0.31224989991991997, 0.0]
FAR = [0.0, 1.0, 0.0]

def _svc(db, **kw):
    # embed_fn maps a 1-byte tag to a canned vector, so no torch needed
    vecs = {b"A": A, b"N": NEAR_A, b"F": FAR}
    return SpeakerService(db, embed_fn=lambda pcm: vecs[pcm], now_fn=lambda: "t",
                          accept=0.9, adapt=0.97, cap=5, top_k=1, **kw)

def test_matching_voice_verified():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    verified, score = _svc(db).identify("k", b"A", "phone", auth_is_kadir=True)
    assert verified is True and score == 1.0

def test_different_voice_not_verified():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    verified, score = _svc(db).identify("k", b"F", "phone", auth_is_kadir=True)
    assert verified is False

def test_high_confidence_adapts_and_persists():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    _svc(db).identify("k", b"A", "headset", auth_is_kadir=True)     # score 1.0 >= adapt
    assert len(load_profile(db, "k").adaptive) == 1
    assert load_profile(db, "k").adaptive[0]["device_hint"] == "headset"

def test_accepted_but_below_adapt_does_not_feed():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    verified, score = _svc(db).identify("k", b"N", "phone", auth_is_kadir=True)
    assert score == 0.95                                            # accept(0.9) <= score < adapt(0.97)
    assert verified is True
    assert load_profile(db, "k").adaptive == []                    # not fed (poisoning guard band)

def test_no_adapt_when_not_authed_kadir():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    _svc(db).identify("k", b"A", "phone", auth_is_kadir=False)      # high score but not authed
    assert load_profile(db, "k").adaptive == []

def test_score_exactly_at_accept_is_verified():
    """Boundary: accept is inclusive (`score >= accept`), not exclusive. An
    identical anchor/query vector gives an exact cosine of 1.0 (no floating
    point rounding: dot=1.0, both norms=1.0). Pinning accept to that same
    1.0 lands the comparison exactly on the boundary. adapt is set out of
    reach (>1.0, cosine's max) so this test isolates the accept check only."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = SpeakerService(db, embed_fn=lambda pcm: {b"A": A}[pcm], now_fn=lambda: "t",
                          accept=1.0, adapt=1.5, cap=5, top_k=1)
    verified, score = svc.identify("k", b"A", "phone", auth_is_kadir=True)
    assert score == 1.0
    assert verified is True

def test_score_exactly_at_adapt_feeds_profile():
    """Boundary: adapt is inclusive (`score >= adapt`), not exclusive. Same
    identical-vector trick as the accept boundary test above, this time
    pinning adapt itself to the exact score so the self-feed condition lands
    on equality rather than strictly above."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = SpeakerService(db, embed_fn=lambda pcm: {b"A": A}[pcm], now_fn=lambda: "t",
                          accept=1.0, adapt=1.0, cap=5, top_k=1)
    svc.identify("k", b"A", "phone", auth_is_kadir=True)
    assert len(load_profile(db, "k").adaptive) == 1
