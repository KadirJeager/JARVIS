from app.speaker import SpeakerProfile

A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]

def _clock():
    seq = iter(["t0", "t1", "t2", "t3", "t4", "t5", "t6"])
    return lambda: next(seq)

def test_score_top_k_cosine_to_gallery():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    assert p.score(A, top_k=1) == 1.0            # identical -> 1.0
    assert p.score(B, top_k=1) == 0.0            # orthogonal -> 0.0

def test_score_uses_both_anchor_and_adaptive():
    p = SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "phone", "ts": "t"}])
    # query close to B should score high via the adaptive sample
    assert p.score([0.0, 0.99, 0.0], top_k=1) > 0.99

def test_adapt_appends_adaptive_sample():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    p.adapt(B, "headset", cap=5, now_fn=lambda: "t1")
    assert len(p.adaptive) == 1
    assert p.adaptive[0] == {"vec": B, "device_hint": "headset", "ts": "t1"}

def test_adapt_evicts_oldest_over_cap_but_keeps_anchors():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    clk = _clock()
    for _ in range(4):
        p.adapt(B, "phone", cap=2, now_fn=clk)
    assert len(p.adaptive) == 2                  # capped
    assert p.anchors == [A]                       # anchors never touched
    assert p.adaptive[-1]["ts"] == "t3"          # newest kept
    assert p.adaptive[0]["ts"] == "t2"           # oldest two evicted

def test_score_top_k_mean_not_max():
    """Verify score uses MEAN of top-k, not max. Top-2 sims are [1.0, 0.8] -> mean 0.9."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.8, 0.6, 0.0]  # cosine sim with [1,0,0] is 0.8 / sqrt(0.64+0.36) = 0.8
    query = [1.0, 0.0, 0.0]
    p = SpeakerProfile(anchors=[v1, v2], adaptive=[])
    score = p.score(query, top_k=2)
    # Top 2 sims: 1.0 (query vs v1) and 0.8 (query vs v2) -> mean = 0.9
    assert abs(score - 0.9) < 0.001, f"Expected mean ~0.9, got {score}"
    assert score != 1.0  # Ensure it's not max

def test_score_top_k_truncates_low_similarity_in_larger_gallery():
    """The existing top_k_mean_not_max test above uses a gallery of exactly 2
    with top_k=2, so "mean of top-k" and "mean of the whole gallery" are
    indistinguishable there. Here the gallery has 3 vectors and top_k=2, with
    the third vector's similarity CLEARLY lower (0.0, orthogonal) -- proving
    it is genuinely excluded, not just coincidentally absent."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.8, 0.6, 0.0]  # cosine to query = 0.8 (as in test_score_top_k_mean_not_max)
    v3 = [0.0, 1.0, 0.0]  # cosine to query = 0.0 -- clearly lower, must be dropped
    query = [1.0, 0.0, 0.0]
    p = SpeakerProfile(anchors=[v1, v2, v3], adaptive=[])
    score = p.score(query, top_k=2)
    assert abs(score - 0.9) < 0.001, f"Expected top-2 mean ~0.9, got {score}"
    whole_gallery_mean = (1.0 + 0.8 + 0.0) / 3
    assert abs(score - whole_gallery_mean) > 0.01  # v3 must not have diluted the mean

def test_score_empty_gallery():
    """Empty gallery (no anchors, no adaptive) should score 0.0."""
    p = SpeakerProfile(anchors=[], adaptive=[])
    assert p.score([1.0, 0.0], top_k=1) == 0.0

def test_adapt_cap_zero_empties_adaptive():
    """With cap=0, adaptive set should be emptied."""
    p = SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "phone", "ts": "t0"}])
    assert len(p.adaptive) == 1
    p.adapt(A, "headset", cap=0, now_fn=lambda: "t1")
    # Should append then evict to cap=0
    assert len(p.adaptive) == 0, f"Expected empty adaptive with cap=0, got {len(p.adaptive)} items"


# --- I6: diversity-preserving eviction (spec §5) ----------------------------

PHONE = [1.0, 0.0, 0.0]
HEADSET = [0.0, 1.0, 0.0]
TABLET = [0.0, 0.0, 1.0]


def _phone_variant(i):
    """Near-identical phone samples: same channel, tiny day-to-day variation."""
    return [1.0, 0.001 * i, 0.0]


def test_adapt_keeps_rare_channels_when_flooded_by_one_device():
    """Spec §5 requires diversity-preserving eviction, "salt recency değil,
    çünkü recency drift'e açık". With pure recency, twenty phone utterances
    evict every headset/tablet sample and the gallery stops covering channels by
    itself -- the exact claim §5 makes. Redundancy-based eviction discards one
    of the twenty near-duplicates instead."""
    clk = iter(f"t{i}" for i in range(100))
    p = SpeakerProfile(anchors=[], adaptive=[])
    p.adapt(HEADSET, "headset", cap=4, now_fn=lambda: next(clk))
    p.adapt(TABLET, "tablet", cap=4, now_fn=lambda: next(clk))
    for i in range(20):
        p.adapt(_phone_variant(i), "phone", cap=4, now_fn=lambda: next(clk))

    assert len(p.adaptive) == 4
    hints = {a["device_hint"] for a in p.adaptive}
    assert "headset" in hints, "the only headset sample was evicted by phone traffic"
    assert "tablet" in hints, "the only tablet sample was evicted by phone traffic"
    assert "phone" in hints, "the newest channel must still be represented"


def test_adapt_evicts_the_redundant_sample_not_merely_the_oldest():
    """Directly pins the eviction rule: the sample whose nearest neighbour is
    closest goes, even when it is the NEWEST one."""
    clk = iter(f"t{i}" for i in range(100))
    p = SpeakerProfile(anchors=[], adaptive=[])
    p.adapt(HEADSET, "headset", cap=2, now_fn=lambda: next(clk))
    p.adapt(TABLET, "tablet", cap=2, now_fn=lambda: next(clk))
    p.adapt(TABLET, "tablet", cap=2, now_fn=lambda: next(clk))   # duplicate of #2
    # one of the two identical tablet vectors must go, never the unique headset
    assert [a["device_hint"] for a in p.adaptive] == ["headset", "tablet"]


def test_adapt_never_evicts_anchors_even_when_they_are_the_redundant_ones():
    """Anchors are the anti-drift baseline: adaptation may only ever shrink the
    adaptive set, never the anchor set, however redundant an anchor looks."""
    clk = iter(f"t{i}" for i in range(100))
    p = SpeakerProfile(anchors=[PHONE, PHONE], adaptive=[])
    for i in range(6):
        p.adapt(_phone_variant(i), "phone", cap=2, now_fn=lambda: next(clk))
    assert p.anchors == [PHONE, PHONE]
    assert len(p.adaptive) == 2


def test_anchor_score_ignores_adaptive_samples():
    """anchor_score is the ADAPT gate's metric: it must not see the adaptive
    set at all, otherwise the gate can be ratcheted (see test_speaker_service)."""
    p = SpeakerProfile(anchors=[PHONE], adaptive=[{"vec": TABLET, "device_hint": "t", "ts": "t0"}])
    assert p.score(TABLET, top_k=1) == 1.0        # full gallery: matches the adaptive sample
    assert p.anchor_score(TABLET, top_k=1) == 0.0  # anchors only: no match


# --- I2 (second half): the lazy model load must be race-free ----------------


def test_get_model_loads_exactly_once_under_concurrent_first_calls(monkeypatch):
    """Speaker identification now runs via asyncio.to_thread (voice.py), so two
    utterances really can reach a cold _model concurrently. Unguarded, both
    would download and build the ~89 MB ECAPA model. Loading it twice doubles
    memory and cold-start latency and leaves the loser's instance referenced by
    an in-flight embed."""
    import sys
    import threading
    import time
    import types

    from app import speaker

    loads = []

    class FakeEncoderClassifier:
        @staticmethod
        def from_hparams(**kwargs):
            loads.append(kwargs)
            time.sleep(0.05)      # widen the window a second loader could enter
            return object()

    pkg = types.ModuleType("speechbrain")
    inference = types.ModuleType("speechbrain.inference")
    speaker_mod = types.ModuleType("speechbrain.inference.speaker")
    speaker_mod.EncoderClassifier = FakeEncoderClassifier
    inference.speaker = speaker_mod
    pkg.inference = inference
    monkeypatch.setitem(sys.modules, "speechbrain", pkg)
    monkeypatch.setitem(sys.modules, "speechbrain.inference", inference)
    monkeypatch.setitem(sys.modules, "speechbrain.inference.speaker", speaker_mod)
    # monkeypatch restores the real _model afterwards, so the torch venv's
    # embedding tests still get the real model whatever the test order is.
    monkeypatch.setattr(speaker, "_model", None)

    n = 8
    start = threading.Barrier(n)
    results = []

    def worker():
        start.wait()              # all threads hit the cold path together
        results.append(speaker._get_model())

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(loads) == 1, f"ECAPA model built {len(loads)} times"
    assert len(results) == n and len(set(map(id, results))) == 1
