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
