"""Firestore persistence for the speaker voiceprint gallery (spec §5).

One document per user: speaker_profiles/{user_id} = {anchors: [[float]...],
adaptive: [{vec, device_hint, ts}...]}. Embeddings are stored as plain float
lists (not firestore Vector): verification is in-process cosine over a small
bounded gallery, so no find_nearest / vector index is needed."""
from .speaker import SpeakerProfile

_COLLECTION = "speaker_profiles"


def load_profile(db, user_id: str) -> SpeakerProfile:
    snap = db.collection(_COLLECTION).document(user_id).get()
    data = snap.to_dict() if snap.exists else {}
    return SpeakerProfile(
        anchors=list(data.get("anchors", [])),
        adaptive=list(data.get("adaptive", [])),
    )


def save_profile(db, user_id: str, profile: SpeakerProfile) -> None:
    db.collection(_COLLECTION).document(user_id).set(
        {"anchors": profile.anchors, "adaptive": profile.adaptive}
    )


def enroll_anchors(db, user_id: str, vecs: list[list[float]]) -> None:
    """Append bootstrap enrollment samples as immutable anchors."""
    profile = load_profile(db, user_id)
    profile.anchors.extend(vecs)
    save_profile(db, user_id, profile)
