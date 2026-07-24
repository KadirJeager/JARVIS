"""Speaker identity: adaptive voiceprint gallery + ECAPA embedding + service
(Katman 2b Dilim 3a, spec §5). SpeakerProfile is PURE (no torch) so gallery
math is unit-testable without the model; embed()/SpeakerService live below and
lazily load torch."""
from typing import Callable

from .memory import _cosine_similarity  # DRY: reuse Katman-1 cosine


class SpeakerProfile:
    """Kadir's voiceprint as a gallery: immutable anchors (bootstrap enrollment,
    the true baseline) + a bounded adaptive set (self-fed high-confidence
    utterances, channel-tagged). Verification scores against anchors ∪ adaptive;
    adaptation only ever grows/evicts the adaptive set (anchors are the anti-drift
    anchor). See spec §5."""

    def __init__(self, anchors: list[list[float]], adaptive: list[dict]):
        self.anchors = anchors
        self.adaptive = adaptive

    def all_vectors(self) -> list[list[float]]:
        return list(self.anchors) + [a["vec"] for a in self.adaptive]

    @staticmethod
    def _top_k_mean(vec: list[float], gallery: list[list[float]], top_k: int) -> float:
        sims = sorted((_cosine_similarity(vec, g) for g in gallery), reverse=True)
        if not sims:
            return 0.0
        chosen = sims[: max(1, top_k)]
        return sum(chosen) / len(chosen)

    def score(self, vec: list[float], top_k: int) -> float:
        """Mean of the top_k cosine similarities to gallery vectors -- robust to
        day-to-day / illness / channel variation because the gallery spans
        conditions. Empty gallery scores 0.0."""
        return self._top_k_mean(vec, self.all_vectors(), top_k)

    def anchor_score(self, vec: list[float], top_k: int) -> float:
        """Same metric, but against the IMMUTABLE anchors only. This is what the
        self-feed (ADAPT) decision must use: scoring the adapt gate against the
        full gallery is a poisoning ratchet -- once an attacker lands a single
        adaptive sample, their similarity to their OWN sample dominates the
        top-k and pushes every later attempt further above the gate. Anchors
        cannot be moved by adaptation, so this gate cannot be ratcheted."""
        return self._top_k_mean(vec, list(self.anchors), top_k)

    def _evict_most_redundant(self) -> None:
        """Drop the adaptive sample that contributes least NEW information: the
        one whose nearest neighbour anywhere else in the gallery (anchors or
        other adaptive samples) is closest. Ties break toward the oldest.

        Spec §5 asks for diversity-preserving eviction explicitly, "salt recency
        değil, çünkü recency drift'e açık": with pure recency, twenty utterances
        from one channel evict every headset/tablet sample and the gallery stops
        covering channels by itself. Redundancy-based eviction keeps the lone
        sample from a rarely used device (nothing near it) and discards one of
        the twenty near-duplicates instead."""
        if not self.adaptive:
            return
        anchors = list(self.anchors)
        worst_i, worst_redundancy = 0, None
        for i, sample in enumerate(self.adaptive):
            others = anchors + [a["vec"] for j, a in enumerate(self.adaptive) if j != i]
            # No neighbours at all -> nothing is redundant; -1.0 keeps it below
            # any real cosine so such a sample is evicted last.
            redundancy = max((_cosine_similarity(sample["vec"], o) for o in others), default=-1.0)
            if worst_redundancy is None or redundancy > worst_redundancy:
                worst_i, worst_redundancy = i, redundancy
        self.adaptive.pop(worst_i)

    def adapt(self, vec: list[float], device_hint: str, cap: int, now_fn: Callable[[], str]) -> None:
        """Append a verified sample to the adaptive set, then evict down to cap
        by redundancy (see _evict_most_redundant). Anchors are never touched.
        Caller is responsible for the ADAPT-threshold + auth gating (see
        SpeakerService)."""
        self.adaptive.append({"vec": vec, "device_hint": device_hint, "ts": now_fn()})
        while len(self.adaptive) > cap:
            self._evict_most_redundant()


import os
import threading

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Lazy singleton ECAPA-TDNN. Loaded once per process, on first embed()
    call, on CPU. Import path + embedding dim confirmed in Task 1.

    Double-checked locking, NOT decoration: identification runs via
    asyncio.to_thread (voice.py), so two utterances really can reach a cold
    _model concurrently. Unguarded, both would download and build the ~89 MB
    model, doubling memory and cold-start latency and leaving whichever
    instance lost the race referenced by an in-flight embed. The unlocked fast
    path keeps the warm case free of lock traffic."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from speechbrain.inference.speaker import EncoderClassifier

            _model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.environ.get("SPEAKER_MODEL_DIR", "/tmp/spkrec-ecapa"),
                run_opts={"device": "cpu"},
            )
    return _model


def pcm16_to_tensor(pcm: bytes):
    """Raw PCM16 mono 16kHz bytes -> float32 tensor [1, samples] in [-1, 1].
    bytearray() makes the buffer writable so torch.frombuffer is happy."""
    import torch

    ints = torch.frombuffer(bytearray(pcm), dtype=torch.int16)
    return (ints.float() / 32768.0).unsqueeze(0)


def embed(pcm: bytes) -> list[float]:
    """192-dim ECAPA speaker embedding for one utterance's PCM16 16kHz audio."""
    emb = _get_model().encode_batch(pcm16_to_tensor(pcm))  # [1, 1, 192]
    return emb.squeeze().tolist()


from datetime import datetime, timezone

from . import speaker_store


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SpeakerService:
    """Orchestrates one utterance's identity check: embed -> score against the
    user's gallery -> verified?(>=accept) -> conditionally self-feed (>=adapt AND
    authed as Kadir) -> persist. embed_fn is injectable so the logic is testable
    without torch. Two thresholds create a poisoning-guard band: accept <= score
    < adapt means "trust it but don't learn from it" (spec §5)."""

    def __init__(self, db, embed_fn=embed, now_fn=_utc_now,
                 accept: float = 0.35, adapt: float = 0.6, cap: int = 20, top_k: int = 3):
        self.db = db
        self.embed_fn = embed_fn
        self.now_fn = now_fn
        self.accept = accept
        self.adapt = adapt
        self.cap = cap
        self.top_k = top_k
        # identify() now runs in a worker thread (voice.py's asyncio.to_thread),
        # so two live connections for the same user can reach the gallery's
        # load -> adapt -> save read-modify-write at once and silently drop one
        # of the two new samples. Serializing just that window costs nothing
        # (it is pure dict/list work plus one Firestore write); the expensive
        # part -- embedding -- stays outside it and fully parallel.
        self._gallery_lock = threading.Lock()

    def identify(self, user_id: str, pcm: bytes, device_hint: str,
                 auth_is_kadir: bool) -> tuple[bool, float]:
        vec = self.embed_fn(pcm)
        with self._gallery_lock:
            profile = speaker_store.load_profile(self.db, user_id)
            # ACCEPT is scored against the WHOLE gallery (that is the point of
            # the gallery: it spans days, health, devices). ADAPT is scored
            # against the anchors ONLY -- see SpeakerProfile.anchor_score for
            # why using the full gallery here turns one successful poisoning
            # sample into a ratchet.
            score = profile.score(vec, self.top_k)
            anchor_score = profile.anchor_score(vec, self.top_k)
            verified = score >= self.accept
            adapted = anchor_score >= self.adapt and auth_is_kadir
            if adapted:
                profile.adapt(vec, device_hint, self.cap, self.now_fn)
                speaker_store.save_profile(self.db, user_id, profile)
        import logging
        logging.info(
            "speaker.identify: user=%s score=%.4f anchor_score=%.4f verified=%s "
            "adapted=%s device=%s anchors=%d adaptive=%d accept=%.2f adapt=%.2f",
            user_id, score, anchor_score, verified, adapted, device_hint,
            len(profile.anchors), len(profile.adaptive), self.accept, self.adapt,
        )
        return verified, score
