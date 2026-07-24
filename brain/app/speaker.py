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
            self.adaptive = self.adaptive[max(0, len(self.adaptive) - cap):]


import os

_model = None


def _get_model():
    """Lazy singleton ECAPA-TDNN. Loaded once per process, on first embed()
    call, on CPU. Import path + embedding dim confirmed in Task 1."""
    global _model
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
