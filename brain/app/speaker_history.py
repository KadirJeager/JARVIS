"""Firestore persistence for the speaker verification history (Dilim 3d spec
§4.2). One document per user: speaker_history/{user_id} = {entries: [...]},
oldest first, kept as a ring buffer -- record() appends and trims to the cap.

Entries carry the utterance EMBEDDING (vec), never audio: the vector is what
makes a later "bu bendim" correction able to feed the gallery, and it cannot
be inverted back into listenable audio. Storing audio would be a categorically
different privacy liability and is out of scope (spec §4.2)."""

_COLLECTION = "speaker_history"


def load_history(db, user_id: str) -> list[dict]:
    snap = db.collection(_COLLECTION).document(user_id).get()
    data = snap.to_dict() if snap.exists else {}
    return list(data.get("entries", []))


def save_history(db, user_id: str, entries: list[dict]) -> None:
    db.collection(_COLLECTION).document(user_id).set({"entries": entries})


def record(db, user_id: str, entry: dict, cap: int) -> None:
    """Append one verification outcome, keeping only the newest `cap`.

    Explicit guard for cap <= 0: negative-slice trap list[-0:] == list[:] (whole
    list), which would disable the bound entirely (same as SPEAKER_BARGE_IN_ONSET_BYTES
    in config.py). cap=0 means "keep no history"."""
    entries = load_history(db, user_id)
    entries.append(entry)
    # Guard: if cap <= 0, keep no history (save empty list); otherwise keep newest cap
    trimmed = [] if cap <= 0 else entries[-cap:]
    save_history(db, user_id, trimmed)


def delete_history(db, user_id: str) -> None:
    db.collection(_COLLECTION).document(user_id).delete()
