"""Tiered memory skeleton (North Star §4.5, §8): profile, facts, lessons, session snapshots."""
import math
from datetime import datetime, timezone
from typing import Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def make_embed_fn() -> Callable[[str], list[float]]:
    """Real embedding factory: google-genai `gemini-embedding-001`, fixed to 768 dims
    (matches the Firestore vector index; see task-10-brief.md Step 4 note)."""
    from google import genai

    client = genai.Client()  # GOOGLE_API_KEY env'den

    def embed(text: str) -> list[float]:
        res = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config={"output_dimensionality": 768},
        )
        return list(res.embeddings[0].values)

    return embed


class Memory:
    """Return strings like "kaydedildi" are intentionally Turkish: they are tool
    outputs the agent relays to the (Turkish-speaking) user, not internal API text."""

    def __init__(self, db, embed_fn: Callable[[str], list[float]] | None = None):
        self.db = db
        self.embed_fn = embed_fn

    # -- Tier 3 (long-term): user profile (§8.1)
    def get_profile(self) -> dict:
        snap = self.db.collection("profile").document("main").get()
        return snap.to_dict() if snap.exists else {}

    def update_profile(self, patch: dict) -> dict:
        self.db.collection("profile").document("main").set(patch, merge=True)
        return self.get_profile()

    # -- Tier 3 (long-term): explicit facts / preferences
    def remember_fact(self, fact: str) -> str:
        record = {"text": fact, "ts": _now()}
        self._attach_embedding(record, fact, "facts")
        self.db.collection("facts").add(record)
        return "kaydedildi"

    # -- Tier 3 (long-term): lesson log (§8.2)
    def add_lesson(self, context: str, tried: str, went_wrong: str, correct: str) -> str:
        record = {
            "context": context, "tried": tried,
            "went_wrong": went_wrong, "correct": correct, "ts": _now(),
        }
        self._attach_embedding(record, " ".join([context, tried, went_wrong, correct]), "lessons")
        self.db.collection("lessons").add(record)
        return "ders kaydedildi"

    def _attach_embedding(self, record: dict, text: str, collection_name: str) -> None:
        """Add an `embedding` field to `record` when embed_fn is configured.
        Real Firestore vector fields must be wrapped in `Vector`; the fake
        (test) path stores a plain list for pure-Python cosine similarity."""
        if self.embed_fn is None:
            return
        vector = self.embed_fn(text)
        collection = self.db.collection(collection_name)
        if hasattr(collection, "find_nearest"):
            from google.cloud.firestore_v1.vector import Vector

            record["embedding"] = Vector(vector)
        else:
            record["embedding"] = vector

    # -- Layer-1 search: substring over facts+lessons, insertion order
    # (recency ordering deliberately deferred). Used when no embed_fn is
    # configured; semantic search (below) replaces this in Task 10.
    def search_memory(self, query: str, top_k: int = 5) -> list[dict]:
        if self.embed_fn is None:
            return self._search_substring(query, top_k)
        return self._search_semantic(query, top_k)

    def _search_substring(self, query: str, top_k: int) -> list[dict]:
        q = query.lower()
        hits = []
        for name in ("facts", "lessons"):
            for snap in self.db.collection(name).stream():
                data = snap.to_dict()
                text = " ".join(str(v) for v in data.values())
                if q in text.lower():
                    hits.append({"source": name, "text": text})
        return hits[:top_k]

    def _search_semantic(self, query: str, top_k: int) -> list[dict]:
        query_vector = self.embed_fn(query)
        if hasattr(self.db.collection("facts"), "find_nearest"):
            return self._search_semantic_native(query_vector, top_k)
        scored = []
        for name in ("facts", "lessons"):
            for snap in self.db.collection(name).stream():
                data = snap.to_dict()
                embedding = data.get("embedding")
                if embedding is None:
                    continue
                text = " ".join(str(v) for k, v in data.items() if k != "embedding")
                score = _cosine_similarity(query_vector, embedding)
                scored.append((score, {"source": name, "text": text}))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [hit for _, hit in scored[:top_k]]

    def _search_semantic_native(self, query_vector: list[float], top_k: int) -> list[dict]:
        """Real-Firestore ANN path via `find_nearest`. Not exercised by the
        fake-DB test suite (FakeCollection has no find_nearest) — kept
        intentionally thin: build the KNN query and read back plain dicts."""
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        hits = []
        for name in ("facts", "lessons"):
            query = self.db.collection(name).find_nearest(
                vector_field="embedding",
                query_vector=Vector(query_vector),
                distance_measure=DistanceMeasure.COSINE,
                limit=top_k,
            )
            for snap in query.stream():
                data = snap.to_dict()
                text = " ".join(str(v) for k, v in data.items() if k != "embedding")
                hits.append({"source": name, "text": text})
        return hits[:top_k]

    # -- Tier 2 (short-term): session snapshot (§4.5)
    def snapshot_session(self, session_id: str, user_id: str, summary: dict) -> None:
        self.db.collection("sessions").document(session_id).set(
            {"user_id": user_id, "ts": _now(), **summary}
        )


class FirestoreAudit:
    def __init__(self, db):
        self.db = db

    def write(self, entry: dict) -> None:
        self.db.collection("audit_log").add(entry)
