"""Tiered memory skeleton (North Star §4.5, §8): profile, facts, lessons, session snapshots."""
import math
import threading
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


def _merge_ranked(hits: list[tuple[float, dict]], top_k: int) -> list[dict]:
    """Sort (distance, hit) pairs ascending -- Firestore COSINE distance: smaller
    means closer -- and truncate once to top_k. Pure and DB-free so the
    cross-collection merge logic can be unit-tested without a real find_nearest."""
    hits.sort(key=lambda pair: pair[0])
    return [hit for _, hit in hits[:top_k]]


_E5_MODEL_NAME = "intfloat/multilingual-e5-base"

_e5_model = None
_e5_model_lock = threading.Lock()


def _get_e5_model():
    """Lazy singleton SentenceTransformer. Loaded once per process, on the
    first embed call -- NOT at module import and NOT at factory time: the
    unit-test venv has no torch at all (so a top-level `import
    sentence_transformers` would break every test that imports this module),
    and main._init() must stay fast (the ~1.1 GB weight load belongs to the
    first real embed, exactly like speaker._get_model()'s ECAPA pattern).

    Double-checked locking, same reasoning as speaker._get_model: memory
    writes and searches run off the event loop, so two cold callers really
    can race here; unguarded, both would build the model. The unlocked fast
    path keeps the warm case free of lock traffic."""
    global _e5_model
    if _e5_model is not None:
        return _e5_model
    with _e5_model_lock:
        if _e5_model is None:
            from sentence_transformers import SentenceTransformer

            _e5_model = SentenceTransformer(_E5_MODEL_NAME)
    return _e5_model


class E5Embedders:
    """The two e5 embedding entry points as SEPARATE named methods, because
    the e5 family is asymmetric by contract: documents must be encoded with
    a "passage: " prefix and queries with a "query: " prefix (intfloat model
    card). One name, one meaning: `embed_passage` is ONLY for text being
    stored, `embed_query` ONLY for search text -- swapping them silently
    degrades recall without raising anything.

    normalize_embeddings=True makes every vector unit-norm, so Firestore's
    COSINE index and the fake-DB cosine path both see properly scaled input.
    multilingual-e5-base is natively 768-dim -- the same dimensionality the
    existing Firestore vector index was built for, so no index change."""

    def embed_passage(self, text: str) -> list[float]:
        vec = _get_e5_model().encode("passage: " + text, normalize_embeddings=True)
        return vec.tolist()

    def embed_query(self, text: str) -> list[float]:
        vec = _get_e5_model().encode("query: " + text, normalize_embeddings=True)
        return vec.tolist()


def make_e5_embedders() -> E5Embedders:
    """Real embedding factory: local `intfloat/multilingual-e5-base` via
    sentence-transformers, replacing the old google-genai
    `gemini-embedding-001` path (same 768 dims as the Firestore vector
    index). Cheap to call -- the heavy model load is deferred to the first
    embed (see _get_e5_model), so main._init() can wire this eagerly."""
    return E5Embedders()


class Memory:
    """Return strings like "kaydedildi" are intentionally Turkish: they are tool
    outputs the agent relays to the (Turkish-speaking) user, not internal API text."""

    def __init__(
        self,
        db,
        embed_fn: Callable[[str], list[float]] | None = None,
        embed_query_fn: Callable[[str], list[float]] | None = None,
    ):
        """Two embed callables, not one, because the e5 model family needs
        DIFFERENT prefixes per role: text being stored is a "passage: ...",
        text being searched is a "query: ..." (see E5Embedders). One name,
        one meaning: `embed_fn` embeds passages (write path,
        _attach_embedding), `embed_query_fn` embeds queries (read path,
        _search_semantic). `embed_query_fn=None` falls back to `embed_fn` --
        the pre-e5 single-function contract, which every existing test and
        the symmetric fake embedders still rely on."""
        self.db = db
        self.embed_fn = embed_fn
        self.embed_query_fn = embed_query_fn if embed_query_fn is not None else embed_fn

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
        query_vector = self.embed_query_fn(query)
        # Uniform-backend assumption: "facts" and "lessons" always come from the
        # same db (both real Firestore or both the fake), so probing one
        # collection is enough to decide the path for both.
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
        """Real-Firestore ANN path via `find_nearest`. Each collection is queried
        for its own top_k, so the two per-collection result sets must be merged
        by distance (not just concatenated+truncated) before the final
        truncation -- otherwise a collection that fills the quota first can
        silently push out closer hits from the other collection.

        `distance_result_field` makes Firestore return the computed COSINE
        distance as a normal field on the document (verified against the
        installed google-cloud-firestore 2.28.0 source: the name passed here is
        forwarded to `StructuredQuery.FindNearest.distance_result_field`, and
        the server adds it to `response_pb.document.fields`, which
        `_query_response_to_snapshot` decodes into the snapshot like any other
        field -- so it shows up in `snap.to_dict()`, no separate accessor).

        Not exercised end-to-end by the fake-DB test suite (FakeCollection has
        no find_nearest); the merge/sort/truncate step is covered directly via
        `_merge_ranked`."""
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        distance_field = "vector_distance"
        hits: list[tuple[float, dict]] = []
        for name in ("facts", "lessons"):
            query = self.db.collection(name).find_nearest(
                vector_field="embedding",
                query_vector=Vector(query_vector),
                distance_measure=DistanceMeasure.COSINE,
                limit=top_k,
                distance_result_field=distance_field,
            )
            for snap in query.stream():
                data = snap.to_dict()
                # inf fallback: a doc missing its server-computed distance ranks
                # last instead of crashing the sort (source-verified as unlikely)
                distance = data.get(distance_field, float("inf"))
                if distance is None:
                    distance = float("inf")
                text = " ".join(
                    str(v) for k, v in data.items()
                    if k not in ("embedding", distance_field)
                )
                hits.append((distance, {"source": name, "text": text}))
        return _merge_ranked(hits, top_k)

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
