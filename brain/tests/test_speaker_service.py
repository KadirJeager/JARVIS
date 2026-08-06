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
    out = _svc(db).identify("k", b"A", "phone", auth_is_kadir=True)
    assert out.verified is True and out.score == 1.0

def test_different_voice_not_verified():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"F", "phone", auth_is_kadir=True)
    assert out.verified is False

def test_high_confidence_adapts_and_persists():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    _svc(db).identify("k", b"A", "headset", auth_is_kadir=True, cm_ok=True)     # score 1.0 >= adapt
    assert len(load_profile(db, "k").adaptive) == 1
    assert load_profile(db, "k").adaptive[0]["device_hint"] == "headset"

def test_accepted_but_below_adapt_does_not_feed():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"N", "phone", auth_is_kadir=True)
    assert out.score == 0.95                                        # accept(0.9) <= score < adapt(0.97)
    assert out.verified is True
    assert load_profile(db, "k").adaptive == []                    # not fed (poisoning guard band)

def test_no_adapt_when_not_authed_kadir():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"A", "phone", auth_is_kadir=False)  # high score but not authed
    assert out.verified is True                  # verification is independent of the adapt auth-gate
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
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True)
    assert out.score == 1.0
    assert out.verified is True

def test_score_exactly_at_adapt_feeds_profile():
    """Boundary: adapt is inclusive (`score >= adapt`), not exclusive. Same
    identical-vector trick as the accept boundary test above, this time
    pinning adapt itself to the exact score so the self-feed condition lands
    on equality rather than strictly above."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = SpeakerService(db, embed_fn=lambda pcm: {b"A": A}[pcm], now_fn=lambda: "t",
                          accept=1.0, adapt=1.0, cap=5, top_k=1)
    svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)
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
    from app.speaker import make_sample
    profile.adaptive.append(make_sample(FAR, "auto", "phone", "t0", "landed"))
    from app.speaker_store import save_profile
    save_profile(db, "k", profile)

    svc = SpeakerService(db, embed_fn=lambda pcm: FAR, now_fn=lambda: "t1",
                         accept=0.9, adapt=0.97, cap=5, top_k=1)
    out = svc.identify("k", b"F", "phone", auth_is_kadir=True)

    assert out.score == 1.0      # full gallery: matches their own landed sample
    assert out.verified is True  # ACCEPT stays a full-gallery decision (by design)
    after = load_profile(db, "k")
    assert len(after.adaptive) == 1, "the attacker fed the profile again -- ratchet is open"
    assert after.adaptive[0]["ts"] == "t0"


def test_empty_anchor_gallery_never_self_feeds():
    """No enrollment -> no baseline -> nothing may be learned. Otherwise the
    very first caller would define the voiceprint."""
    db = FakeDB()
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t",
                         accept=0.9, adapt=0.97, cap=5, top_k=1)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True)
    assert (out.verified, out.score) == (False, 0.0)
    assert load_profile(db, "k").adaptive == []


# --- Dilim 3d: identify() outcome + history recording ------------------------

def test_identify_returns_adapted_sample_id_when_it_feeds():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"A", "headset", auth_is_kadir=True, cm_ok=True)
    assert out.adapted_sample_id is not None
    assert load_profile(db, "k").adaptive[0]["id"] == out.adapted_sample_id
    assert out.vec == A


def test_identify_adapted_sample_id_is_none_in_the_guard_band():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"N", "phone", auth_is_kadir=True)
    assert out.verified is True and out.adapted_sample_id is None


def test_record_history_appends_a_spec_shaped_entry_and_honors_the_cap():
    from app import speaker_history
    db = FakeDB()
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t0",
                         accept=0.9, adapt=0.97, cap=5, top_k=1,
                         id_fn=lambda: "h1", history_cap=2)
    svc.record_history("k", score=0.8, verified=True, vec=A, device_hint="phone",
                       presence="locked", trust_level="MEDIUM",
                       adapted_sample_id=None)
    entry = speaker_history.load_history(db, "k")[0]
    assert entry == {"id": "h1", "ts": "t0", "score": 0.8, "verified": True,
                     "device_hint": "phone", "presence": "locked",
                     "trust_level": "MEDIUM", "adapted_sample_id": None,
                     "correction": None, "cm_fake_prob": None, "vec": A}
    for _ in range(3):
        svc.record_history("k", score=0.1, verified=False, vec=A,
                           device_hint="phone", presence="locked",
                           trust_level="LOW", adapted_sample_id=None)
    assert len(speaker_history.load_history(db, "k")) == 2   # history_cap wired


def test_production_service_carries_the_config_history_cap(monkeypatch):
    """The 3a lesson, third time proven on that branch: a knob that exists but
    is not passed by the accessor production calls is a green-suite lie. Pin
    get_speaker_service itself.

    config.SPEAKER_HISTORY_CAP's default (50) coincidentally equals
    SpeakerService.__init__'s own history_cap default, so asserting against
    the out-of-the-box default would not actually catch the wiring being
    dropped (verified empirically: it did not fail that mutation). Monkeypatch
    config to a distinctive value so the assertion is load-bearing."""
    import app.main as main_mod
    from app import config
    from tests.fakes import FakeDB as _FakeDB
    monkeypatch.setattr(config, "SPEAKER_HISTORY_CAP", 7)
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: _FakeDB(), raising=False)
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    svc = main_mod.get_speaker_service()
    assert svc.history_cap == 7
    from app.speaker import new_sample_id
    assert svc.id_fn is new_sample_id


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
    svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)

    assert observed["locked_during_embed"] is False
    assert observed["locked_during_save"] is True


def test_update_sample_holds_the_gallery_lock_across_its_save(monkeypatch):
    from app import speaker as speaker_mod
    db = FakeDB(); enroll_anchors(db, "k", [A], id_fn=lambda: "a1")
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t",
                         accept=0.9, adapt=0.97, cap=5, top_k=1,
                         labels=frozenset({"hasta"}))
    observed = {}
    real_save = speaker_mod.speaker_store.save_profile

    def watching_save(db_, user_id, profile):
        observed["locked"] = svc._gallery_lock.locked()
        return real_save(db_, user_id, profile)

    monkeypatch.setattr(speaker_mod.speaker_store, "save_profile", watching_save)
    svc.update_sample("k", "a1", label="hasta")
    assert observed["locked"] is True


# --- Dilim 3d spec §5+§6: corrections --------------------------------------

from app import speaker_history


def _hist_entry(i, vec, *, adapted=None, correction=None):
    return {"id": f"e{i}", "ts": f"t{i}", "score": 0.5, "verified": True,
            "device_hint": "phone", "presence": "locked", "trust_level": "MEDIUM",
            "adapted_sample_id": adapted, "correction": correction, "vec": vec}


def _mgmt_svc(db, manual_cap=5):
    ids = iter(f"id{i}" for i in range(100))
    return SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "now",
                         accept=0.9, adapt=0.97, cap=5, top_k=1,
                         id_fn=lambda: next(ids), manual_cap=manual_cap)


def test_confirm_adds_a_manual_sample_and_marks_the_entry():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    result = svc.confirm_history("k", "e1")
    assert result["already"] is False
    profile = load_profile(db, "k")
    manuals = [s for s in profile.adaptive if s["source"] == "manual"]
    assert len(manuals) == 1 and manuals[0]["vec"] == FAR
    assert manuals[0]["id"] == result["added_sample_id"]
    assert profile.anchors[0]["source"] == "enroll", "manual must NEVER become an anchor"
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] == "confirmed"
    assert entry["adapted_sample_id"] == result["added_sample_id"]


def test_confirm_is_idempotent_and_does_not_burn_the_cap():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    first = svc.confirm_history("k", "e1")
    second = svc.confirm_history("k", "e1")
    assert second["already"] is True
    assert second["added_sample_id"] == first["added_sample_id"]
    profile = load_profile(db, "k")
    assert sum(1 for s in profile.adaptive if s["source"] == "manual") == 1


def test_the_sixth_manual_sample_is_refused_with_the_count():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    from app.speaker import make_sample
    profile = load_profile(db, "k")
    for i in range(5):
        profile.adaptive.append(make_sample(FAR, "manual", "phone", "t", f"m{i}"))
    from app.speaker_store import save_profile
    save_profile(db, "k", profile)
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    import pytest as _pytest
    from app.speaker import RuleViolation
    with _pytest.raises(RuleViolation, match="5/5"):
        svc.confirm_history("k", "e1")
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] is None, "a refused confirm must not mark the entry"


def test_reject_removes_the_adapted_sample_and_marks_the_entry():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = _mgmt_svc(db)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)   # adapts (score 1.0)
    assert out.adapted_sample_id is not None
    # identify() recorded nothing (that is voice.py's job) -- seed the entry:
    speaker_history.record(db, "k", _hist_entry(1, A, adapted=out.adapted_sample_id), cap=50)
    result = svc.reject_history("k", "e1")
    assert result["removed_sample_id"] == out.adapted_sample_id
    assert load_profile(db, "k").adaptive == []
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] == "rejected" and entry["adapted_sample_id"] is None


def test_reject_of_a_never_adapted_entry_just_marks_it():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    result = svc.reject_history("k", "e1")
    assert result == {"removed_sample_id": None, "already": False}
    assert speaker_history.load_history(db, "k")[0]["correction"] == "rejected"


def test_reject_is_idempotent():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    svc.reject_history("k", "e1")
    assert svc.reject_history("k", "e1")["already"] is True


def test_mind_can_be_changed_in_both_directions():
    """spec §6: reversing is legitimate use. confirm -> reject removes the
    manual sample; reject -> confirm adds a fresh one."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    added = svc.confirm_history("k", "e1")["added_sample_id"]
    removed = svc.reject_history("k", "e1")["removed_sample_id"]
    assert removed == added
    assert [s for s in load_profile(db, "k").adaptive if s["source"] == "manual"] == []
    re_added = svc.confirm_history("k", "e1")["added_sample_id"]
    assert re_added is not None and re_added != added
    manuals = [s for s in load_profile(db, "k").adaptive if s["source"] == "manual"]
    assert [s["id"] for s in manuals] == [re_added]


def test_unknown_history_entry_raises_not_found():
    import pytest as _pytest
    from app.speaker import SampleNotFound
    db = FakeDB()
    svc = _mgmt_svc(db)
    with _pytest.raises(SampleNotFound):
        svc.confirm_history("k", "yok")
    with _pytest.raises(SampleNotFound):
        svc.reject_history("k", "yok")


def test_manual_sample_votes_in_accept_but_never_referees_adapt():
    """spec §5's core split, mutation-verified by construction: the probe F is
    FAR from the anchor A but IDENTICAL to the manual sample, so
    - ACCEPT (full gallery, top_k=1) scores 1.0 -> verified: manual VOTES;
    - ADAPT (anchors only) scores 0.0 -> no self-feed: manual cannot REFEREE.
    If anchor_score ever read the full gallery, the auto sample added here
    would prove it (adaptive would grow)."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    svc.confirm_history("k", "e1")                    # manual sample = FAR
    svc2 = SpeakerService(db, embed_fn=lambda pcm: FAR, now_fn=lambda: "t",
                          accept=0.9, adapt=0.9, cap=5, top_k=1)
    out = svc2.identify("k", b"F", "phone", auth_is_kadir=True)
    assert out.verified is True and out.score == 1.0          # manual voted
    assert out.adapted_sample_id is None                       # ...but did not referee
    profile = load_profile(db, "k")
    assert sum(1 for s in profile.adaptive if s["source"] == "auto") == 0


# --- Final review Finding 1: confirm on an already-adapted entry must PROMOTE
# the live auto sample in place, never append a duplicate --------------------


def test_confirm_on_auto_adapted_entry_promotes_in_place():
    """When identify() already self-fed the utterance (adapted_sample_id
    points at a live auto sample), confirm must relabel THAT sample manual
    rather than append a second copy of the same vector -- a duplicate would
    double-count in top-k ACCEPT scoring and burn a manual-cap slot for data
    already in the gallery."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = _mgmt_svc(db)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)  # adapts (score 1.0)
    auto_id = out.adapted_sample_id
    assert auto_id is not None
    speaker_history.record(db, "k", _hist_entry(1, A, adapted=auto_id), cap=50)

    result = svc.confirm_history("k", "e1")

    assert result["already"] is False
    assert result["added_sample_id"] == auto_id
    profile = load_profile(db, "k")
    matching = [s for s in profile.adaptive if s["vec"] == A]
    assert len(matching) == 1, "confirm must not duplicate the vec"
    assert matching[0]["id"] == auto_id
    assert matching[0]["source"] == "manual"
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] == "confirmed"
    assert entry["adapted_sample_id"] == auto_id, "link must stay pointed at the promoted sample"
    assert sum(1 for s in profile.adaptive if s["source"] == "manual") == 1
    assert sum(1 for s in profile.adaptive if s["source"] == "auto") == 0


def test_confirm_then_reject_of_an_auto_adapted_entry_leaves_no_trace_of_the_vec():
    """The reviewer's repro, end to end. Before promote-in-place, confirm
    appended a DUPLICATE manual sample and overwrote the entry's link to
    point at the duplicate, so a later reject removed only the duplicate --
    the ORIGINAL auto sample (same vec) survived and kept voting even though
    the user said "bu ben değildim" (spec §6: reject must remove the sample
    the utterance became; confirm->reject reversal is explicitly legitimate).
    This test is RED against the pre-fix code."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = _mgmt_svc(db)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)
    auto_id = out.adapted_sample_id
    assert auto_id is not None
    speaker_history.record(db, "k", _hist_entry(1, A, adapted=auto_id), cap=50)

    svc.confirm_history("k", "e1")
    svc.reject_history("k", "e1")

    profile = load_profile(db, "k")
    assert [s for s in profile.adaptive if s["vec"] == A] == [], (
        "the confirmed-then-rejected utterance must leave NO sample voting, "
        "auto or manual")


def test_confirm_falls_back_to_append_when_the_linked_sample_is_gone():
    """The link can outlive the sample it points at (eviction, deletion). The
    vec captured in the history entry at record time is then the only
    surviving copy, so confirm must fall back to today's append-from-vec
    path -- there is nothing left to promote in place."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = _mgmt_svc(db)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)
    auto_id = out.adapted_sample_id
    assert auto_id is not None
    speaker_history.record(db, "k", _hist_entry(1, A, adapted=auto_id), cap=50)

    from app.speaker_store import save_profile
    profile = load_profile(db, "k")
    profile.adaptive = [s for s in profile.adaptive if s["id"] != auto_id]  # simulate eviction
    save_profile(db, "k", profile)

    result = svc.confirm_history("k", "e1")

    assert result["added_sample_id"] != auto_id, "a fresh id, not the gone one"
    manuals = [s for s in load_profile(db, "k").adaptive if s["source"] == "manual"]
    assert len(manuals) == 1
    assert manuals[0]["id"] == result["added_sample_id"]
    assert manuals[0]["vec"] == A


def test_cap_refused_promote_leaves_the_entry_unmarked_and_sample_auto():
    """Cap-check ordering: promoting an auto sample still increases the
    manual count by one, so a full manual cap must refuse the promote --
    leaving the entry uncorrected and the sample still "auto" -- exactly the
    same rule the append path already follows."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    from app.speaker import make_sample, RuleViolation
    from app.speaker_store import save_profile
    profile = load_profile(db, "k")
    for i in range(5):
        profile.adaptive.append(make_sample(FAR, "manual", "phone", "t", f"m{i}"))
    save_profile(db, "k", profile)

    svc = _mgmt_svc(db)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)  # adapts despite full manual cap
    auto_id = out.adapted_sample_id
    assert auto_id is not None
    speaker_history.record(db, "k", _hist_entry(1, A, adapted=auto_id), cap=50)

    import pytest as _pytest
    with _pytest.raises(RuleViolation, match="5/5"):
        svc.confirm_history("k", "e1")

    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] is None, "a refused promote must not mark the entry"
    saved = [s for s in load_profile(db, "k").adaptive if s["id"] == auto_id]
    assert len(saved) == 1 and saved[0]["source"] == "auto", "unpromoted"


# -- allow_adapt: the server's own echo guard (2026-08-03) ---------------------
# Belt-and-braces behind the client-side half-duplex guard. If any client ever
# streams audio again while the device is speaking Jarvis's reply, that audio
# may still be SCORED (so trust degrades honestly) but must never be allowed to
# feed the gallery -- a self-feeding TTS voice is the one failure this system
# cannot recover from on its own.

def test_allow_adapt_false_blocks_self_feeding_even_at_a_perfect_score():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"A", "phone", auth_is_kadir=True, allow_adapt=False)
    assert out.verified is True and out.score == 1.0     # still scored honestly
    assert out.adapted_sample_id is None
    assert load_profile(db, "k").adaptive == []          # gallery untouched


def test_allow_adapt_defaults_to_true_so_existing_callers_are_unchanged():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    _svc(db).identify("k", b"A", "phone", auth_is_kadir=True, cm_ok=True)
    assert len(load_profile(db, "k").adaptive) == 1
