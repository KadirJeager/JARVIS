"""Speaker identity: adaptive voiceprint gallery + ECAPA embedding + service
(Katman 2b Dilim 3a, spec §5). SpeakerProfile is PURE (no torch) so gallery
math is unit-testable without the model; embed()/SpeakerService live below and
lazily load torch."""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .memory import _cosine_similarity  # DRY: reuse Katman-1 cosine


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_sample_id() -> str:
    """Stable sample identity (spec §4.1): list position CANNOT be the id --
    eviction shifts positions, and a client's "delete the 3rd sample" would hit
    the wrong one. uuid in production, injectable in tests."""
    return uuid.uuid4().hex


def make_sample(vec, source, device_hint, ts, sample_id, label=None, note=None) -> dict:
    """The ONE sample shape anchors and adaptive share (spec §4.1)."""
    return {"id": sample_id, "vec": list(vec), "source": source, "ts": ts,
            "device_hint": device_hint, "label": label, "note": note}


def _normalize_sample(raw, default_source: str) -> dict:
    """Accept the pre-3d shapes (bare vector for anchors, {vec, device_hint,
    ts} for adaptive) and fill in the 3d fields. Backward reading only:
    everything SAVED goes out as full samples."""
    if isinstance(raw, dict):
        return {
            "id": raw.get("id") or new_sample_id(),
            "vec": list(raw["vec"]),
            "source": raw.get("source") or default_source,
            "ts": raw.get("ts"),
            "device_hint": raw.get("device_hint", "unknown"),
            "label": raw.get("label"),
            "note": raw.get("note"),
        }
    return make_sample(raw, default_source, "unknown", None, new_sample_id())


class SampleNotFound(KeyError):
    """Requested sample/history id does not exist (endpoint maps to 404 --
    it may have been deleted, evicted, or dropped off the ring buffer)."""


class RuleViolation(ValueError):
    """A management rule refused the operation (endpoint maps to 400).
    Carries the Turkish user-facing message (spec §10)."""


_UNSET = object()


@dataclass
class IdentifyOutcome:
    """One utterance's identity verdict plus what the verification history
    needs to make it correctable later (spec §4.2): the embedding itself and,
    if the utterance fed the gallery, the id of the sample it became."""
    verified: bool
    score: float
    vec: list[float]
    adapted_sample_id: str | None


class SpeakerProfile:
    """Kadir's voiceprint as a gallery: immutable anchors (bootstrap enrollment,
    the true baseline) + a bounded adaptive set (self-fed high-confidence
    utterances, channel-tagged). Verification scores against anchors ∪ adaptive;
    adaptation only ever grows/evicts the adaptive set (anchors are the anti-drift
    anchor). See spec §5."""

    def __init__(self, anchors: list, adaptive: list):
        self.anchors = [_normalize_sample(a, "enroll") for a in anchors]
        self.adaptive = [_normalize_sample(a, "auto") for a in adaptive]

    def all_vectors(self) -> list[list[float]]:
        return [s["vec"] for s in self.anchors] + [s["vec"] for s in self.adaptive]

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
        return self._top_k_mean(vec, [s["vec"] for s in self.anchors], top_k)

    def _evict_most_redundant(self) -> None:
        """Drop the AUTO adaptive sample that contributes least NEW information
        (nearest neighbour anywhere else in the gallery is closest; ties break
        toward the oldest). Manual samples are exempt: they are user-curated
        (spec §5 -- their guarantee is revocability, so only an explicit DELETE
        or a reject correction removes one), but they still count as
        neighbours, so an auto near-duplicate OF a manual sample is redundant.

        Spec §5 (3a) asks for diversity-preserving eviction explicitly: with
        pure recency, twenty utterances from one channel evict every
        headset/tablet sample and the gallery stops covering channels."""
        auto_idx = [i for i, s in enumerate(self.adaptive) if s["source"] == "auto"]
        if not auto_idx:
            return
        anchor_vecs = [s["vec"] for s in self.anchors]
        worst_i, worst_redundancy = auto_idx[0], None
        for i in auto_idx:
            others = anchor_vecs + [a["vec"] for j, a in enumerate(self.adaptive) if j != i]
            redundancy = max(
                (_cosine_similarity(self.adaptive[i]["vec"], o) for o in others),
                default=-1.0,
            )
            if worst_redundancy is None or redundancy > worst_redundancy:
                worst_i, worst_redundancy = i, redundancy
        self.adaptive.pop(worst_i)

    def adapt(self, vec: list[float], device_hint: str, cap: int,
              now_fn: Callable[[], str], id_fn: Callable[[], str] = new_sample_id) -> str:
        """Append a verified AUTO sample, then evict auto samples down to cap
        (manual samples have their own cap and lifecycle -- Task 6 / spec §5).
        Returns the new sample's id so the caller can link it from the
        verification history (adapted_sample_id, spec §4.2)."""
        sample = make_sample(vec, "auto", device_hint, now_fn(), id_fn())
        self.adaptive.append(sample)
        while sum(1 for s in self.adaptive if s["source"] == "auto") > cap:
            self._evict_most_redundant()
        return sample["id"]


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


from . import speaker_history, speaker_store


class SpeakerService:
    """Orchestrates one utterance's identity check: embed -> score against the
    user's gallery -> verified?(>=accept) -> conditionally self-feed (>=adapt AND
    authed as Kadir) -> persist. embed_fn is injectable so the logic is testable
    without torch. Two thresholds create a poisoning-guard band: accept <= score
    < adapt means "trust it but don't learn from it" (spec §5)."""

    def __init__(self, db, embed_fn=embed, now_fn=_utc_now, id_fn=new_sample_id,
                 accept: float = 0.35, adapt: float = 0.6, cap: int = 20, top_k: int = 3,
                 history_cap: int = 50, labels: frozenset = frozenset(),
                 manual_cap: int = 5):
        self.db = db
        self.embed_fn = embed_fn
        self.now_fn = now_fn
        self.id_fn = id_fn
        self.accept = accept
        self.adapt = adapt
        self.cap = cap
        self.top_k = top_k
        self.history_cap = history_cap
        self.labels = labels
        self.manual_cap = manual_cap
        # identify() now runs in a worker thread (voice.py's asyncio.to_thread),
        # so two live connections for the same user can reach the gallery's
        # load -> adapt -> save read-modify-write at once and silently drop one
        # of the two new samples. Serializing just that window costs nothing
        # (it is pure dict/list work plus one Firestore write); the expensive
        # part -- embedding -- stays outside it and fully parallel.
        self._gallery_lock = threading.Lock()

    def enroll(self, user_id: str, vecs: list[list[float]],
               device_hint: str = "unknown") -> int:
        """Bootstrap enrollment (POST /api/voice/enroll) under the SAME lock
        identify() uses. Returns the anchor count actually persisted.

        speaker_store.enroll_anchors is a load -> extend -> save read-modify-
        write, exactly like identify()'s adapt path. Until the speaker work
        moved inference off the event loop, BOTH ran to completion on that one
        loop with no await inside their windows, so interleaving was
        structurally impossible and the store needed no lock. asyncio.to_thread
        made the race real -- an enroll landing between identify()'s load and
        save (or vice versa) silently loses one side's write. Sharing this lock
        restores the invariant WITHIN THIS PROCESS. It is not a distributed
        lock: speaker_store.save_profile is a blind .set(), so two Cloud Run
        instances writing the same gallery still lose one side's write. That is
        acceptable only because this is a single-user deployment pinned to
        --min-instances 1; it is not a general guarantee.

        The count is re-read from storage inside the lock rather than derived
        from the vectors we just sent, so the number returned to the client is
        the number Firestore actually holds."""
        with self._gallery_lock:
            speaker_store.enroll_anchors(
                self.db, user_id, vecs,
                device_hint=device_hint, now_fn=self.now_fn, id_fn=self.id_fn,
            )
            return len(speaker_store.load_profile(self.db, user_id).anchors)

    def identify(self, user_id: str, pcm: bytes, device_hint: str,
                 auth_is_kadir: bool) -> IdentifyOutcome:
        vec = self.embed_fn(pcm)
        adapted_sample_id = None
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
            if anchor_score >= self.adapt and auth_is_kadir:
                adapted_sample_id = profile.adapt(
                    vec, device_hint, self.cap, self.now_fn, self.id_fn)
                speaker_store.save_profile(self.db, user_id, profile)
        import logging
        logging.info(
            "speaker.identify: user=%s score=%.4f anchor_score=%.4f verified=%s "
            "adapted=%s device=%s anchors=%d adaptive=%d accept=%.2f adapt=%.2f",
            user_id, score, anchor_score, verified, adapted_sample_id is not None,
            device_hint, len(profile.anchors), len(profile.adaptive),
            self.accept, self.adapt,
        )
        return IdentifyOutcome(verified=verified, score=score, vec=vec,
                               adapted_sample_id=adapted_sample_id)

    def overview(self, user_id: str) -> tuple:
        """One CONSISTENT read of gallery + history for GET /api/voice/profile:
        under the lock so a concurrent adapt/correction cannot land between the
        two loads and show a history row pointing at a sample that "does not
        exist yet" (spec §10)."""
        with self._gallery_lock:
            return (speaker_store.load_profile(self.db, user_id),
                    speaker_history.load_history(self.db, user_id))

    def _find_sample(self, profile: SpeakerProfile, sample_id: str) -> dict:
        for s in profile.anchors + profile.adaptive:
            if s["id"] == sample_id:
                return s
        raise SampleNotFound(sample_id)

    def update_sample(self, user_id: str, sample_id: str, *,
                      label=_UNSET, note=_UNSET) -> dict:
        """PATCH semantics: only the fields the caller actually sent change
        (label=None is a deliberate clear, absent means untouched)."""
        with self._gallery_lock:
            profile = speaker_store.load_profile(self.db, user_id)
            sample = self._find_sample(profile, sample_id)
            if label is not _UNSET:
                if label is not None and label not in self.labels:
                    raise RuleViolation(
                        f"Geçersiz etiket: {label}. Geçerli etiketler: "
                        + ", ".join(sorted(self.labels)))
                sample["label"] = label
            if note is not _UNSET:
                sample["note"] = note
            speaker_store.save_profile(self.db, user_id, profile)
            return dict(sample)

    def delete_sample(self, user_id: str, sample_id: str) -> None:
        """Single-sample deletion, anchors included -- EXCEPT the last anchor
        (spec §8): an anchorless profile cannot score and ADAPT refereeing
        loses its reference; whole-profile deletion is the endpoint for that."""
        with self._gallery_lock:
            profile = speaker_store.load_profile(self.db, user_id)
            self._find_sample(profile, sample_id)          # 404 wins over 400
            is_anchor = any(s["id"] == sample_id for s in profile.anchors)
            if is_anchor and len(profile.anchors) == 1:
                raise RuleViolation(
                    "Son çapa silinemez: çapasız profil ses doğrulayamaz. "
                    "Profili tamamen kaldırmak için profil silmeyi kullan.")
            profile.anchors = [s for s in profile.anchors if s["id"] != sample_id]
            profile.adaptive = [s for s in profile.adaptive if s["id"] != sample_id]
            speaker_store.save_profile(self.db, user_id, profile)

    def record_history(self, user_id: str, *, score: float, verified: bool,
                       vec: list[float], device_hint: str, presence: str,
                       trust_level: str, adapted_sample_id: str | None) -> str:
        """Append one utterance's verification outcome to the history ring
        buffer (spec §4.2). Under the gallery lock: corrections (Task 6) read
        and write history and gallery TOGETHER, so every mutation of either
        serializes on the one lock (spec §10)."""
        entry = {
            "id": self.id_fn(), "ts": self.now_fn(), "score": score,
            "verified": verified, "device_hint": device_hint,
            "presence": presence, "trust_level": trust_level,
            "adapted_sample_id": adapted_sample_id, "correction": None,
            "vec": vec,
        }
        with self._gallery_lock:
            speaker_history.record(self.db, user_id, entry, self.history_cap)
        return entry["id"]

    @staticmethod
    def _find_entry(entries: list[dict], entry_id: str) -> dict:
        for e in entries:
            if e["id"] == entry_id:
                return e
        raise SampleNotFound(entry_id)

    def confirm_history(self, user_id: str, entry_id: str) -> dict:
        """"Bu bendim" (spec §6): the stored embedding becomes a MANUAL gallery
        sample -- it VOTES in ACCEPT but never REFEREES adapt (it lives in the
        adaptive list, and anchor_score reads anchors only, spec §5).
        Idempotent via entry.correction; adapted_sample_id doubles as the link
        for a later reversal."""
        with self._gallery_lock:
            entries = speaker_history.load_history(self.db, user_id)
            entry = self._find_entry(entries, entry_id)
            if entry.get("correction") == "confirmed":
                return {"added_sample_id": entry.get("adapted_sample_id"),
                        "already": True}
            profile = speaker_store.load_profile(self.db, user_id)
            manual_count = sum(
                1 for s in profile.adaptive if s["source"] == "manual")
            if manual_count >= self.manual_cap:
                raise RuleViolation(
                    f"Elle eklenen örnek sınırı dolu ({manual_count}/{self.manual_cap}). "
                    "Yenisini eklemek için önce elle eklenmiş bir örneği sil.")
            sample = make_sample(entry["vec"], "manual",
                                 entry.get("device_hint", "unknown"),
                                 self.now_fn(), self.id_fn())
            profile.adaptive.append(sample)
            entry["correction"] = "confirmed"
            entry["adapted_sample_id"] = sample["id"]
            speaker_store.save_profile(self.db, user_id, profile)
            speaker_history.save_history(self.db, user_id, entries)
            return {"added_sample_id": sample["id"], "already": False}

    def reject_history(self, user_id: str, entry_id: str) -> dict:
        """"Bu ben değildim" (spec §6): if the utterance fed the gallery
        (auto-adapt or an earlier confirm), that sample is removed -- it may
        already be gone via eviction/deletion, which is fine, the marking is
        what idempotency rests on. The sample is by construction never an
        anchor, so the last-anchor rule cannot be tripped from here."""
        with self._gallery_lock:
            entries = speaker_history.load_history(self.db, user_id)
            entry = self._find_entry(entries, entry_id)
            if entry.get("correction") == "rejected":
                return {"removed_sample_id": None, "already": True}
            removed = None
            target = entry.get("adapted_sample_id")
            if target:
                profile = speaker_store.load_profile(self.db, user_id)
                before = len(profile.adaptive)
                profile.adaptive = [s for s in profile.adaptive if s["id"] != target]
                if len(profile.adaptive) != before:
                    removed = target
                    speaker_store.save_profile(self.db, user_id, profile)
            entry["correction"] = "rejected"
            entry["adapted_sample_id"] = None
            speaker_history.save_history(self.db, user_id, entries)
            return {"removed_sample_id": removed, "already": False}
