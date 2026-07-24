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

    def score(self, vec: list[float], top_k: int) -> float:
        """Mean of the top_k cosine similarities to gallery vectors -- robust to
        day-to-day / illness / channel variation because the gallery spans
        conditions. Empty gallery scores 0.0."""
        sims = sorted((_cosine_similarity(vec, g) for g in self.all_vectors()), reverse=True)
        if not sims:
            return 0.0
        chosen = sims[: max(1, top_k)]
        return sum(chosen) / len(chosen)

    def adapt(self, vec: list[float], device_hint: str, cap: int, now_fn: Callable[[], str]) -> None:
        """Append a verified sample to the adaptive set; evict oldest (recency
        order) once over cap. Anchors are never touched. Caller is responsible
        for the ADAPT-threshold + auth gating (see SpeakerService)."""
        self.adaptive.append({"vec": vec, "device_hint": device_hint, "ts": now_fn()})
        if len(self.adaptive) > cap:
            self.adaptive = self.adaptive[-cap:]
