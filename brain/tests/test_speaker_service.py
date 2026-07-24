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
    verified, score = _svc(db).identify("k", b"A", "phone", auth_is_kadir=False)  # high score but not authed
    assert verified is True                     # verification is independent of the adapt auth-gate
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


# --- M1: the self-feed gate must not be ratchetable -------------------------

def test_adapt_gate_ignores_adaptive_samples_so_poisoning_cannot_ratchet():
    """Scoring the ADAPT gate against the whole gallery is a ratchet: once an
    attacker lands ONE adaptive sample, their similarity to their own sample
    dominates the top-k, so every later attempt clears the gate more easily and
    walks the profile toward them. The gate is scored against the immutable
    anchors, which adaptation cannot move, so a landed sample buys nothing."""
    db = FakeDB()
    enroll_anchors(db, "k", [A])
    # simulate one already-landed attacker sample
    profile = load_profile(db, "k")
    profile.adaptive.append({"vec": FAR, "device_hint": "phone", "ts": "t0"})
    from app.speaker_store import save_profile
    save_profile(db, "k", profile)

    svc = SpeakerService(db, embed_fn=lambda pcm: FAR, now_fn=lambda: "t1",
                         accept=0.9, adapt=0.97, cap=5, top_k=1)
    verified, score = svc.identify("k", b"F", "phone", auth_is_kadir=True)

    assert score == 1.0          # full gallery: matches their own landed sample
    assert verified is True      # ACCEPT stays a full-gallery decision (by design)
    after = load_profile(db, "k")
    assert len(after.adaptive) == 1, "the attacker fed the profile again -- ratchet is open"
    assert after.adaptive[0]["ts"] == "t0"


def test_empty_anchor_gallery_never_self_feeds():
    """No enrollment -> no baseline -> nothing may be learned. Otherwise the
    very first caller would define the voiceprint."""
    db = FakeDB()
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t",
                         accept=0.9, adapt=0.97, cap=5, top_k=1)
    verified, score = svc.identify("k", b"A", "phone", auth_is_kadir=True)
    assert (verified, score) == (False, 0.0)
    assert load_profile(db, "k").adaptive == []


def test_gallery_read_modify_write_is_serialized_but_embedding_is_not(monkeypatch):
    """identify() runs in a worker thread now (voice.py's asyncio.to_thread), so
    two live connections for the same user can hit load -> adapt -> save at the
    same time and silently drop one of the two new samples. The lock must cover
    exactly that window -- and NOT the embedding, which is the seconds-long part
    that must stay parallel."""
    from app import speaker as speaker_mod

    db = FakeDB()
    enroll_anchors(db, "k", [A])
    observed = {}

    def slow_embed(pcm):
        observed["locked_during_embed"] = svc._gallery_lock.locked()
        return A

    svc = SpeakerService(db, embed_fn=slow_embed, now_fn=lambda: "t",
                         accept=0.9, adapt=0.97, cap=5, top_k=1)

    real_save = speaker_mod.speaker_store.save_profile
    def watching_save(db_, user_id, profile):
        observed["locked_during_save"] = svc._gallery_lock.locked()
        return real_save(db_, user_id, profile)

    monkeypatch.setattr(speaker_mod.speaker_store, "save_profile", watching_save)
    svc.identify("k", b"A", "phone", auth_is_kadir=True)

    assert observed["locked_during_embed"] is False
    assert observed["locked_during_save"] is True
