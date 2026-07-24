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
