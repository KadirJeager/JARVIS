"""Tiered memory skeleton (North Star §4.5, §8): profile, facts, lessons, session snapshots."""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Memory:
    def __init__(self, db):
        self.db = db

    # -- Kademe 3: user profile (§8.1)
    def get_profile(self) -> dict:
        snap = self.db.collection("profile").document("main").get()
        return snap.to_dict() if snap.exists else {}

    def update_profile(self, patch: dict) -> dict:
        self.db.collection("profile").document("main").set(patch, merge=True)
        return self.get_profile()

    # -- Kademe 3: explicit facts / preferences
    def remember_fact(self, fact: str) -> str:
        self.db.collection("facts").add({"text": fact, "ts": _now()})
        return "kaydedildi"

    # -- Kademe 3: lesson log (§8.2)
    def add_lesson(self, context: str, tried: str, went_wrong: str, correct: str) -> str:
        self.db.collection("lessons").add({
            "context": context, "tried": tried,
            "went_wrong": went_wrong, "correct": correct, "ts": _now(),
        })
        return "ders kaydedildi"

    # -- Katman 1 search: substring over facts+lessons (semantic upgrade: Task 10)
    def search_memory(self, query: str, top_k: int = 5) -> list[dict]:
        q = query.lower()
        hits = []
        for name in ("facts", "lessons"):
            for snap in self.db.collection(name).stream():
                data = snap.to_dict()
                text = " ".join(str(v) for v in data.values())
                if q in text.lower():
                    hits.append({"source": name, "text": text})
        return hits[:top_k]

    # -- Kademe 2: session snapshot (§4.5)
    def snapshot_session(self, session_id: str, user_id: str, summary: dict) -> None:
        self.db.collection("sessions").document(session_id).set(
            {"user_id": user_id, "ts": _now(), **summary}
        )


class FirestoreAudit:
    def __init__(self, db):
        self.db = db

    def write(self, entry: dict) -> None:
        self.db.collection("audit_log").add(entry)
