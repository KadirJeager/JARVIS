"""Firestore persistence for the speaker voiceprint gallery (spec §5, §4.1).

One document per user: speaker_profiles/{user_id} = {anchors: [sample...],
adaptive: [sample...]}, where sample = {id, vec, source, ts, device_hint,
label, note} (see speaker.make_sample). Embeddings are stored as plain float
lists (not firestore Vector): verification is in-process cosine over a small
bounded gallery, so no find_nearest / vector index is needed."""
from .speaker import SpeakerProfile, _utc_now, make_sample, new_sample_id

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


def enroll_anchors(db, user_id: str, vecs: list[list[float]],
                   device_hint: str = "unknown", now_fn=None, id_fn=None) -> None:
    """Append bootstrap enrollment samples as immutable anchors (full sample
    dicts, source="enroll" -- spec §4.1)."""
    now_fn = now_fn or _utc_now
    id_fn = id_fn or new_sample_id
    profile = load_profile(db, user_id)
    ts = now_fn()
    profile.anchors.extend(
        make_sample(v, "enroll", device_hint, ts, id_fn()) for v in vecs
    )
    save_profile(db, user_id, profile)
