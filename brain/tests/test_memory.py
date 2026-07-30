"""Tests for tiered memory skeleton."""
import os
import sys
import types

import pytest

import app.memory as memory_mod
from app.memory import FirestoreAudit, Memory, _cosine_similarity, _merge_ranked, make_e5_embedders
from tests.fakes import FakeDB


def test_profile_roundtrip():
    m = Memory(FakeDB())
    assert m.get_profile() == {}
    m.update_profile({"name": "Kadir", "coffee": "X kafeden"})
    assert m.get_profile()["coffee"] == "X kafeden"


def test_remember_fact_and_search():
    m = Memory(FakeDB())
    m.remember_fact("Kadir salı akşamları aranmak istemiyor")
    hits = m.search_memory("salı")
    assert len(hits) == 1 and "salı" in hits[0]["text"]


def test_add_lesson_shape():
    db = FakeDB()
    m = Memory(db)
    m.add_lesson("sipariş", "eski akış", "buton değişmiş", "yeni akış şu")
    lessons = list(db.collection("lessons").docs.values())
    assert lessons[0]["went_wrong"] == "buton değişmiş"


def test_session_snapshot_written():
    db = FakeDB()
    m = Memory(db)
    m.snapshot_session("s1", "kadir", {"turns": 3, "summary": "tanışma"})
    assert db.collection("sessions").docs["s1"]["summary"] == "tanışma"


def test_firestore_audit_writes_to_audit_log():
    db = FakeDB()
    FirestoreAudit(db).write({"tool": "x", "decision": "allow"})
    assert list(db.collection("audit_log").docs.values())[0]["decision"] == "allow"


def test_search_uses_embed_fn_when_available():
    calls = []

    def fake_embed(text):
        calls.append(text)
        return [1.0, 0.0] if "kahve" in text else [0.0, 1.0]

    db = FakeDB()
    m = Memory(db, embed_fn=fake_embed)
    m.remember_fact("kahveyi X'ten söyler")
    m.remember_fact("salı akşamı arama")
    hits = m.search_memory("kahve nereden")
    assert calls  # embedding gerçekten çağrıldı
    assert "kahve" in hits[0]["text"]


def test_merge_ranked_orders_by_distance_ascending_across_sources():
    # Regression for the cross-collection ranking bug: a "facts" hit with a
    # larger (worse) distance must not out-rank a closer "lessons" hit just
    # because it happened to be appended first.
    hits = [
        (0.9, {"source": "facts", "text": "uzak fact"}),
        (0.1, {"source": "lessons", "text": "yakın lesson"}),
        (0.5, {"source": "facts", "text": "orta fact"}),
    ]
    result = _merge_ranked(hits, top_k=2)
    assert [h["text"] for h in result] == ["yakın lesson", "orta fact"]


def test_merge_ranked_respects_top_k_truncation():
    hits = [(float(i), {"source": "facts", "text": str(i)}) for i in range(5)]
    result = _merge_ranked(hits, top_k=3)
    assert [h["text"] for h in result] == ["0", "1", "2"]


def test_merge_ranked_empty_input():
    assert _merge_ranked([], top_k=5) == []


# -- e5 dual-embedder contract (embed_fn=passages, embed_query_fn=queries)


def test_embed_query_fn_defaults_to_embed_fn():
    """Backward compatibility: callers that inject a single embed_fn (every
    pre-e5 test, and any symmetric fake) get query embedding from the same
    callable, exactly as before the two-role split."""
    def fake_embed(text):
        return [1.0, 0.0]

    m = Memory(FakeDB(), embed_fn=fake_embed)
    assert m.embed_query_fn is fake_embed


def test_attach_uses_embed_fn_and_search_uses_embed_query_fn():
    """The two callables must not be interchangeable: writes embed with
    embed_fn (e5 "passage: " role), searches with embed_query_fn (e5
    "query: " role). Separate fakes prove which one actually fired."""
    passage_calls, query_calls = [], []

    def fake_passage(text):
        passage_calls.append(text)
        return [1.0, 0.0]

    def fake_query(text):
        query_calls.append(text)
        return [1.0, 0.0]

    m = Memory(FakeDB(), embed_fn=fake_passage, embed_query_fn=fake_query)
    m.remember_fact("kahveyi X'ten söyler")
    assert passage_calls == ["kahveyi X'ten söyler"]
    assert query_calls == []  # yazma yolu sorgu embedder'ına dokunmamalı

    hits = m.search_memory("kahve nereden")
    assert query_calls == ["kahve nereden"]
    assert passage_calls == ["kahveyi X'ten söyler"]  # arama, yazma embedder'ına dokunmamalı
    assert "kahve" in hits[0]["text"]


# -- make_e5_embedders (sentence_transformers mocked: no torch, no download)


class _FakeArray:
    """Minimal stand-in for the numpy array encode() returns: production
    code only calls .tolist() on it."""

    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class _FakeSentenceTransformer:
    instances = []

    def __init__(self, model_name):
        self.model_name = model_name
        self.encode_calls = []
        _FakeSentenceTransformer.instances.append(self)

    def encode(self, text, normalize_embeddings=False):
        self.encode_calls.append((text, normalize_embeddings))
        return _FakeArray([0.0] * 768)


@pytest.fixture
def fake_sentence_transformers(monkeypatch):
    """Swap the sentence_transformers import (lazy, inside _get_e5_model)
    for a fake module, and reset the process-lifetime model singleton so
    each test really observes a cold load."""
    _FakeSentenceTransformer.instances.clear()
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setattr(memory_mod, "_e5_model", None)
    return module


def test_e5_factory_is_lazy(fake_sentence_transformers):
    """make_e5_embedders() must NOT load the model: main._init() calls it
    eagerly, and the ~1.1 GB load belongs to the first real embed."""
    make_e5_embedders()
    assert _FakeSentenceTransformer.instances == []


def test_e5_passage_and_query_prefixes_and_normalization(fake_sentence_transformers):
    embedders = make_e5_embedders()
    vec = embedders.embed_passage("Kadir kahveyi X'ten söyler")
    assert vec == [0.0] * 768  # 768-dim passthrough, list[float]
    model = _FakeSentenceTransformer.instances[0]
    assert model.model_name == "intfloat/multilingual-e5-base"
    assert model.encode_calls == [("passage: Kadir kahveyi X'ten söyler", True)]

    embedders.embed_query("kahve nereden")
    assert model.encode_calls[-1] == ("query: kahve nereden", True)


def test_e5_model_is_a_process_singleton(fake_sentence_transformers):
    """Two embeds -- even across roles -- share ONE model instance; a second
    construction would mean a second ~1.1 GB load."""
    embedders = make_e5_embedders()
    embedders.embed_passage("a")
    embedders.embed_query("b")
    make_e5_embedders().embed_passage("c")
    assert len(_FakeSentenceTransformer.instances) == 1


# -- Opt-in live test (real model download ~1.1 GB; skipped in CI/dev unless
# JARVIS_E5_LIVE_TEST=1 and run under the torch-capable .venv-speaker)


@pytest.mark.skipif(
    os.environ.get("JARVIS_E5_LIVE_TEST") != "1",
    reason="live e5 model test -- set JARVIS_E5_LIVE_TEST=1 (torch venv) to run",
)
def test_e5_live_turkish_recall():
    embedders = make_e5_embedders()
    passages = [
        "Kadir kahvesini her sabah X kafeden söylüyor",
        "Veritabanı migrasyonları deploy'dan önce çalıştırılır",
        "Salı akşamları Kadir aranmak istemiyor",
    ]
    passage_vecs = [embedders.embed_passage(p) for p in passages]
    query_vec = embedders.embed_query("Kadir salı akşamı aranabilir mi")

    # (a) native 768-dim on both roles
    assert len(query_vec) == 768
    assert all(len(v) == 768 for v in passage_vecs)

    # (b) deterministic: same input, bit-identical vector
    assert embedders.embed_query("Kadir salı akşamı aranabilir mi") == query_vec

    # (c) mini recall: the on-topic passage must be the nearest hit
    scores = [_cosine_similarity(query_vec, v) for v in passage_vecs]
    assert scores.index(max(scores)) == 2
