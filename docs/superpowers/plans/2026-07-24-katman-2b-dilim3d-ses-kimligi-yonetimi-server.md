# Katman 2b — Dilim 3d (Sunucu): Ses Kimliği Yönetimi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-24-katman-2b-dilim3d-ses-kimligi-yonetimi-design.md`
**Scope:** spec §2'deki **3d-1** (şema + doğrulama geçmişi — Task 1–3) ve **3d-2** (yönetim uçları — Task 4–8). **3d-3 (Android ekranı) bu planda DEĞİL** — ayrı alt sistem, ayrı plan (2b'deki backend/Android ayrımıyla aynı desen).

**Goal:** Make the speaker-identity core (Dilim 3a) visible, correctable, and deletable: a unified sample schema with stable ids, a 50-entry verification history, and six `require_user`-gated management endpoints — all BEFORE any real biometric data exists (spec §3: schema-before-enrollment kills the migration).

**Architecture:** `SpeakerProfile` normalizes both legacy shapes into one sample dict (id/vec/source/ts/device_hint/label/note) at construction, so every existing test and fixture keeps working while everything *saved* is full samples. A new `speaker_history` store keeps a ring buffer of verification outcomes (embedding vectors, never audio). `SpeakerService` grows management methods that all take the existing `_gallery_lock`; a new `voice_manage.py` router exposes them, always via `asyncio.to_thread`. Responses are built by allowlist projection so `vec` structurally cannot leak.

**Tech Stack:** FastAPI + Pydantic (mevcut), Firestore via `FakeDB` in tests, pytest. Torch'a hiç dokunulmaz — tüm testler `.venv`'de (torch-free) koşar, `speaker.embed` zaten monkeypatch'leniyor.

## Global Constraints

- Suite command (NEVER pipe to `tail` — exit-code mask lesson): `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests -q`
- All user-facing strings Turkish; code, comments, commit messages English.
- Every gallery/history **mutation and multi-doc read** runs under `SpeakerService._gallery_lock` and, from an endpoint, off the event loop via `asyncio.to_thread` (spec §10 — both rules were "kanla öğrenildi" in 3a).
- **No `vec` field in any HTTP response body** (spec §6). Responses are built by allowlist projection, and a recursive scan test pins this.
- Env/config names (spec §12): `JARVIS_SPEAKER_HISTORY_CAP` default `50`, `JARVIS_SPEAKER_MANUAL_CAP` default `5`.
- Fixed label set (spec §4.1, ASCII on purpose — they are API values, not UI copy): `saglikli, hasta, yorgun, gurultulu, kulaklik, hoparlor, arac`.
- Sample `source` values: `enroll | auto | manual`. `manual` never becomes an anchor; ADAPT refereeing reads anchors only (spec §5).
- History entry fields are EXACTLY spec §4.2's: `id, ts, score, verified, device_hint, presence, trust_level, adapted_sample_id, correction, vec` — no extras.
- The 3a branch lesson, three times over: **a test that calls a guard proves the guard, not that production wires it.** Every new config knob / provider must be pinned through the accessor production actually reads (Task 3 & 8 wiring tests), and each guard test gets a quick mutation check before commit.
- Existing tests may be UPDATED for the schema change but never weakened: assertions are transformed/extended, not deleted. Every such edit is listed in the task that makes it.

**Task-specific failure modes this plan defends against (CLAUDE.md planning rule):**
1. Schema migration silently weakening old assertions → each edit enumerated, transformation rules given.
2. "Guard exists, production doesn't wire it" (3a'da 3 kez) → wiring tests on `get_speaker_service` params + router mounting via `TestClient(main.app)`.
3. New endpoints blocking the event loop or skipping the shared lock → per-endpoint lock tests reuse 3a's `watching_save` pattern; all handlers `to_thread`.

---

### Task 1: Unified sample schema (SpeakerProfile + store + enroll metadata)

**Files:**
- Modify: `brain/app/speaker.py` (top-of-file helpers; `SpeakerProfile.__init__`, `all_vectors`, `anchor_score`, `_evict_most_redundant`, `adapt`; `SpeakerService.__init__`, `enroll`)
- Modify: `brain/app/speaker_store.py` (`enroll_anchors` signature)
- Modify: `brain/app/main.py` (`EnrollRequest.device_hint`, enroll endpoint pass-through)
- Modify: `brain/tests/test_speaker_service.py:87` (legacy raw append → `make_sample`)
- Modify: `brain/tests/test_speaker_store.py` (vec-projection on anchor equality asserts)
- Test: `brain/tests/test_speaker_schema.py` (new)

**Interfaces:**
- Consumes: existing `SpeakerProfile`, `speaker_store.load_profile/save_profile/enroll_anchors`, `memory._cosine_similarity`.
- Produces (later tasks rely on these exact names):
  - `speaker.new_sample_id() -> str` (uuid4 hex)
  - `speaker.make_sample(vec, source, device_hint, ts, sample_id, label=None, note=None) -> dict`
  - `SpeakerProfile.anchors: list[dict]`, `SpeakerProfile.adaptive: list[dict]` — every element is a full sample dict
  - `SpeakerProfile.adapt(vec, device_hint, cap, now_fn, id_fn=new_sample_id) -> str` (returns new sample id; cap counts `source=="auto"` only)
  - `speaker_store.enroll_anchors(db, user_id, vecs, device_hint="unknown", now_fn=None, id_fn=None)`
  - `SpeakerService.__init__(..., id_fn=new_sample_id)`; `SpeakerService.enroll(user_id, vecs, device_hint="unknown") -> int`

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_speaker_schema.py`:

```python
"""Dilim 3d spec §4.1: anchors and adaptive share ONE sample shape with stable
ids. Legacy shapes (bare vectors / {vec, device_hint, ts}) still LOAD -- dev-time
fixtures exist in them -- but everything SAVED is full samples. Production has no
data yet (spec §3), so no migration path beyond this normalization is needed."""
from app.speaker import SpeakerProfile, make_sample
from app.speaker_store import enroll_anchors, load_profile, save_profile
from tests.fakes import FakeDB

A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]


def test_legacy_shapes_normalize_to_full_samples():
    p = SpeakerProfile(anchors=[A],
                       adaptive=[{"vec": B, "device_hint": "phone", "ts": "t0"}])
    a = p.anchors[0]
    assert a["vec"] == A and a["source"] == "enroll" and a["id"]
    assert a["label"] is None and a["note"] is None and a["device_hint"] == "unknown"
    ad = p.adaptive[0]
    assert ad["vec"] == B and ad["source"] == "auto" and ad["id"]
    assert ad["device_hint"] == "phone" and ad["ts"] == "t0"


def test_sample_ids_are_distinct_and_survive_a_save_load_round_trip():
    db = FakeDB()
    p = SpeakerProfile(anchors=[A, B], adaptive=[])
    ids = [s["id"] for s in p.anchors]
    assert len(set(ids)) == 2
    save_profile(db, "k", p)
    assert [s["id"] for s in load_profile(db, "k").anchors] == ids


def test_scoring_is_unchanged_by_the_schema():
    """The schema is metadata-only: cosine math must see exactly the same
    vectors as before."""
    p = SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "p", "ts": "t"}])
    assert p.score(A, top_k=1) == 1.0
    assert p.anchor_score(B, top_k=1) == 0.0     # adaptive is invisible to ADAPT


def test_enroll_anchors_writes_full_samples_with_device_hint():
    db = FakeDB()
    ids = iter(["i1", "i2"])
    enroll_anchors(db, "k", [A, B], device_hint="phone",
                   now_fn=lambda: "t1", id_fn=lambda: next(ids))
    anchors = load_profile(db, "k").anchors
    assert [a["id"] for a in anchors] == ["i1", "i2"]
    assert all(a["source"] == "enroll" and a["device_hint"] == "phone"
               and a["ts"] == "t1" for a in anchors)


def test_adapt_returns_the_new_sample_id():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    sid = p.adapt(B, "phone", cap=5, now_fn=lambda: "t", id_fn=lambda: "new-id")
    assert sid == "new-id"
    assert p.adaptive[0]["id"] == "new-id" and p.adaptive[0]["source"] == "auto"


def test_eviction_never_removes_a_manual_sample():
    """spec §5: a manual sample is user-curated -- only an explicit DELETE or a
    reject correction removes it. cap=1 leaves one auto slot; the manual
    near-duplicate of the anchor would be THE most redundant sample under the
    old rule, so surviving here is discriminating."""
    manual = make_sample(A, "manual", "phone", "t0", "m1")
    p = SpeakerProfile(anchors=[A], adaptive=[manual])
    p.adapt(A, "phone", cap=1, now_fn=lambda: "t1", id_fn=lambda: "auto1")
    p.adapt(B, "phone", cap=1, now_fn=lambda: "t2", id_fn=lambda: "auto2")
    assert [s["id"] for s in p.adaptive if s["source"] == "manual"] == ["m1"]
    assert sum(1 for s in p.adaptive if s["source"] == "auto") == 1


def test_adaptive_cap_counts_only_auto_samples():
    """The auto cap (20 in prod) and the manual cap (5, Task 6) are separate
    budgets: a manual sample must not consume an auto slot."""
    manual = make_sample(B, "manual", "phone", "t0", "m1")
    p = SpeakerProfile(anchors=[A], adaptive=[manual])
    p.adapt(A, "phone", cap=1, now_fn=lambda: "t1", id_fn=lambda: "auto1")
    assert len(p.adaptive) == 2          # manual + 1 auto, nothing evicted
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_speaker_schema.py -v`
Expected: FAIL / ERROR — `ImportError: cannot import name 'make_sample'`.

- [ ] **Step 3: Implement the schema in `speaker.py`**

At the top of `brain/app/speaker.py`, immediately after `from .memory import _cosine_similarity`, add (these MUST be defined before the mid-file `from . import speaker_store` at ~line 130, because `speaker_store` will import them at its module top):

```python
import uuid
from datetime import datetime, timezone


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
```

Then delete the now-duplicate block further down the file (the old `from datetime import datetime, timezone` + `_utc_now` definition around lines 128–134 — keep the `from . import speaker_store` line where it is).

Replace `SpeakerProfile.__init__`, `all_vectors`, `anchor_score`, `_evict_most_redundant`, `adapt` (docstrings of unchanged methods stay):

```python
    def __init__(self, anchors: list, adaptive: list):
        self.anchors = [_normalize_sample(a, "enroll") for a in anchors]
        self.adaptive = [_normalize_sample(a, "auto") for a in adaptive]

    def all_vectors(self) -> list[list[float]]:
        return [s["vec"] for s in self.anchors] + [s["vec"] for s in self.adaptive]
```

In `anchor_score`, change the gallery argument to `[s["vec"] for s in self.anchors]`.

```python
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
```

- [ ] **Step 4: Thread the metadata through the store and the enroll path**

`brain/app/speaker_store.py` — module top gains the helper imports; `enroll_anchors` gains metadata params:

```python
from .speaker import SpeakerProfile, _utc_now, make_sample, new_sample_id
```

```python
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
```

`brain/app/speaker.py` — `SpeakerService.__init__` gains `id_fn=new_sample_id` (store as `self.id_fn`); `identify`'s adapt call becomes `profile.adapt(vec, device_hint, self.cap, self.now_fn, self.id_fn)` (still discarding the return until Task 3); `enroll` gains `device_hint`:

```python
    def enroll(self, user_id: str, vecs: list[list[float]],
               device_hint: str = "unknown") -> int:
        with self._gallery_lock:
            speaker_store.enroll_anchors(
                self.db, user_id, vecs,
                device_hint=device_hint, now_fn=self.now_fn, id_fn=self.id_fn,
            )
            return len(speaker_store.load_profile(self.db, user_id).anchors)
```

(keep the existing docstring). `brain/app/main.py` — `EnrollRequest` gains `device_hint: str = "unknown"`, and the enroll endpoint's service call becomes:

```python
        total = await asyncio.to_thread(
            lambda: get_speaker_service().enroll(email, vecs, device_hint=req.device_hint)
        )
```

- [ ] **Step 5: Run the schema tests, then the whole suite; transform (never delete) the breakages**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_speaker_schema.py -v` → all PASS.
Run: `.venv/bin/python -m pytest tests -q` → expected failures ONLY at these known sites; fix exactly as listed:

1. `tests/test_speaker_store.py` — anchor equality asserts (e.g. `p.anchors == [A, B]`, `.anchors == []` stays fine): transform to vec projection, e.g. `[s["vec"] for s in p.anchors] == [A, B]`. Same for any `p.adaptive == [...]` full-dict equality → project `["vec"]` (and keep/extend the `device_hint`/`ts` field asserts).
2. `tests/test_speaker_service.py:87` — raw legacy append bypasses `__init__` normalization and would leave a sample without `id`/`source`. Replace:
   ```python
   profile.adaptive.append({"vec": FAR, "device_hint": "phone", "ts": "t0"})
   ```
   with:
   ```python
   from app.speaker import make_sample
   profile.adaptive.append(make_sample(FAR, "auto", "phone", "t0", "landed"))
   ```
   (the test's `after.adaptive[0]["ts"] == "t0"` assert keeps passing).
3. Any other failure: STOP and diagnose — it is not on this task's expected list, do not "fix forward".

Verify no assertion was weakened: `git diff tests/` must show transformations/additions only.

- [ ] **Step 6: Full suite green + commit**

Run: `.venv/bin/python -m pytest tests -q` → all pass (baseline was 209 passed / 1 skipped; count may only grow).

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/speaker.py brain/app/speaker_store.py brain/app/main.py brain/tests/test_speaker_schema.py brain/tests/test_speaker_store.py brain/tests/test_speaker_service.py
git commit -m "feat(speaker): unify gallery samples into one id-stable schema

Anchors and adaptive now share {id, vec, source, ts, device_hint, label,
note} (Dilim 3d spec 4.1). Legacy shapes normalize on load; eviction and
the adaptive cap now apply to auto samples only, manual samples are
user-curated (spec 5). Enrollment records device_hint."
```

---

### Task 2: Config knobs + verification-history store

**Files:**
- Modify: `brain/app/config.py` (3 new constants)
- Create: `brain/app/speaker_history.py`
- Modify: `brain/app/speaker_store.py` (add `delete_profile`)
- Modify: `brain/tests/fakes.py` (`FakeDoc.delete`)
- Test: `brain/tests/test_speaker_history.py` (new), `brain/tests/test_speaker_store.py` (delete_profile test)

**Interfaces:**
- Consumes: `FakeDB` fake Firestore surface.
- Produces:
  - `config.SPEAKER_HISTORY_CAP: int` (env `JARVIS_SPEAKER_HISTORY_CAP`, default 50)
  - `config.SPEAKER_MANUAL_CAP: int` (env `JARVIS_SPEAKER_MANUAL_CAP`, default 5)
  - `config.SPEAKER_SAMPLE_LABELS: frozenset[str]`
  - `speaker_history.load_history(db, user_id) -> list[dict]`
  - `speaker_history.save_history(db, user_id, entries) -> None`
  - `speaker_history.record(db, user_id, entry, cap) -> None` (ring buffer, oldest dropped)
  - `speaker_history.delete_history(db, user_id) -> None`
  - `speaker_store.delete_profile(db, user_id) -> None`
  - `FakeDoc.delete()`

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_speaker_history.py`:

```python
"""Dilim 3d spec §4.2: speaker_history/{user_id} holds a ring buffer of
verification outcomes. Embeddings are stored, audio never is."""
from app import speaker_history
from tests.fakes import FakeDB


def _entry(i):
    return {"id": f"e{i}", "ts": f"t{i}", "score": 0.5, "verified": True,
            "device_hint": "phone", "presence": "foreground",
            "trust_level": "HIGH", "adapted_sample_id": None,
            "correction": None, "vec": [1.0, 0.0]}


def test_empty_history_loads_as_empty_list():
    assert speaker_history.load_history(FakeDB(), "k") == []


def test_record_appends_in_order():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=50)
    speaker_history.record(db, "k", _entry(2), cap=50)
    assert [e["id"] for e in speaker_history.load_history(db, "k")] == ["e1", "e2"]


def test_ring_buffer_drops_the_oldest_beyond_cap():
    db = FakeDB()
    for i in range(51):
        speaker_history.record(db, "k", _entry(i), cap=50)
    entries = speaker_history.load_history(db, "k")
    assert len(entries) == 50
    assert entries[0]["id"] == "e1" and entries[-1]["id"] == "e50"


def test_history_is_user_keyed():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=50)
    assert speaker_history.load_history(db, "someone@else.com") == []


def test_delete_history_removes_the_document():
    db = FakeDB()
    speaker_history.record(db, "k", _entry(1), cap=50)
    speaker_history.delete_history(db, "k")
    assert speaker_history.load_history(db, "k") == []
    snap = db.collection("speaker_history").document("k").get()
    assert snap.exists is False, "doc must be GONE, not emptied -- deletion is deletion"
```

Append to `brain/tests/test_speaker_store.py`:

```python
def test_delete_profile_removes_the_document():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A])
    from app.speaker_store import delete_profile
    delete_profile(db, "kadir@example.com")
    snap = db.collection("speaker_profiles").document("kadir@example.com").get()
    assert snap.exists is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests/test_speaker_history.py tests/test_speaker_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.speaker_history'` and `ImportError` for `delete_profile`.

- [ ] **Step 3: Implement**

`brain/tests/fakes.py` — add to `FakeDoc`:

```python
    def delete(self):
        self.store.pop(self.key, None)
```

Create `brain/app/speaker_history.py`:

```python
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
    """Append one verification outcome, keeping only the newest `cap`."""
    entries = load_history(db, user_id)
    entries.append(entry)
    save_history(db, user_id, entries[-cap:])


def delete_history(db, user_id: str) -> None:
    db.collection(_COLLECTION).document(user_id).delete()
```

`brain/app/speaker_store.py` — add:

```python
def delete_profile(db, user_id: str) -> None:
    db.collection(_COLLECTION).document(user_id).delete()
```

`brain/app/config.py` — add after the `SPEAKER_BARGE_IN_ONSET_BYTES` block, before `TRUST_STATE_KEY`:

```python
# Speaker identity MANAGEMENT (Katman 2b Dilim 3d). History cap bounds both
# cost and privacy exposure (spec §4.2); the manual cap is an ACCIDENT guard,
# not a security boundary -- the token holder can bypass voice entirely
# anyway (3a spec §12), the real guarantee is revocability (spec §5).
SPEAKER_HISTORY_CAP = int(os.environ.get("JARVIS_SPEAKER_HISTORY_CAP", "50"))
SPEAKER_MANUAL_CAP = int(os.environ.get("JARVIS_SPEAKER_MANUAL_CAP", "5"))
# Closed label set (spec §4.1): a fixed vocabulary is what makes aggregation
# possible ("gurultulu ortamda ortalama skor 0.41"); the free-text `note`
# field catches what the set misses. ASCII on purpose: these are API values,
# not UI copy. Revisited after threshold calibration (spec §12).
SPEAKER_SAMPLE_LABELS = frozenset(
    {"saglikli", "hasta", "yorgun", "gurultulu", "kulaklik", "hoparlor", "arac"}
)
```

- [ ] **Step 4: Run tests, then full suite**

Run: `.venv/bin/python -m pytest tests/test_speaker_history.py tests/test_speaker_store.py -v` → PASS.
Run: `.venv/bin/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/config.py brain/app/speaker_history.py brain/app/speaker_store.py brain/tests/fakes.py brain/tests/test_speaker_history.py brain/tests/test_speaker_store.py
git commit -m "feat(speaker): verification-history store + management config knobs

speaker_history/{user_id} ring buffer (JARVIS_SPEAKER_HISTORY_CAP=50),
JARVIS_SPEAKER_MANUAL_CAP=5, fixed sample label set, delete primitives
(Dilim 3d spec 4.2)."
```

---

### Task 3: IdentifyOutcome + history recording on the live path (3d-1 kapanışı)

**Files:**
- Modify: `brain/app/speaker.py` (`IdentifyOutcome` dataclass; `identify` return; `SpeakerService.__init__` gains `history_cap`; new `record_history`)
- Modify: `brain/app/main.py` (`get_speaker_service` passes `history_cap=config.SPEAKER_HISTORY_CAP`)
- Modify: `brain/app/voice.py` (`_verify_utterance` consumes the outcome and records history)
- Modify: `brain/tests/test_speaker_service.py` (tuple-unpack call sites → attribute access)
- Modify: `brain/tests/test_voice.py` (`FakeSpeaker` returns `IdentifyOutcome` + records `record_history` calls)
- Test: additions in `test_speaker_service.py`, `test_voice.py`, `brain/tests/test_speaker_e2e.py`

**Interfaces:**
- Consumes: Task 1's `adapt() -> str`, Task 2's `speaker_history.record`, `config.SPEAKER_HISTORY_CAP`.
- Produces:
  - `speaker.IdentifyOutcome` dataclass: `verified: bool, score: float, vec: list[float], adapted_sample_id: str | None`
  - `SpeakerService.identify(...) -> IdentifyOutcome` (BREAKING: was `tuple[bool, float]` — every call site updated in this task)
  - `SpeakerService.__init__(..., history_cap: int = 50)`
  - `SpeakerService.record_history(user_id, *, score, verified, vec, device_hint, presence, trust_level, adapted_sample_id) -> str` (returns entry id)

- [ ] **Step 1: Write the failing tests**

Append to `brain/tests/test_speaker_service.py`:

```python
# --- Dilim 3d: identify() outcome + history recording ------------------------

def test_identify_returns_adapted_sample_id_when_it_feeds():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"A", "headset", auth_is_kadir=True)
    assert out.adapted_sample_id is not None
    assert load_profile(db, "k").adaptive[0]["id"] == out.adapted_sample_id
    assert out.vec == A


def test_identify_adapted_sample_id_is_none_in_the_guard_band():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    out = _svc(db).identify("k", b"N", "phone", auth_is_kadir=True)
    assert out.verified is True and out.adapted_sample_id is None


def test_record_history_appends_a_spec_shaped_entry_and_honors_the_cap():
    from app import speaker_history
    db = FakeDB()
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t0",
                         accept=0.9, adapt=0.97, cap=5, top_k=1,
                         id_fn=lambda: "h1", history_cap=2)
    svc.record_history("k", score=0.8, verified=True, vec=A, device_hint="phone",
                       presence="locked", trust_level="MEDIUM",
                       adapted_sample_id=None)
    entry = speaker_history.load_history(db, "k")[0]
    assert entry == {"id": "h1", "ts": "t0", "score": 0.8, "verified": True,
                     "device_hint": "phone", "presence": "locked",
                     "trust_level": "MEDIUM", "adapted_sample_id": None,
                     "correction": None, "vec": A}
    for _ in range(3):
        svc.record_history("k", score=0.1, verified=False, vec=A,
                           device_hint="phone", presence="locked",
                           trust_level="LOW", adapted_sample_id=None)
    assert len(speaker_history.load_history(db, "k")) == 2   # history_cap wired


def test_production_service_carries_the_config_history_cap(monkeypatch):
    """The 3a lesson, third time proven on that branch: a knob that exists but
    is not passed by the accessor production calls is a green-suite lie. Pin
    get_speaker_service itself."""
    import app.main as main_mod
    from app import config
    from tests.fakes import FakeDB as _FakeDB
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: _FakeDB(), raising=False)
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    svc = main_mod.get_speaker_service()
    assert svc.history_cap == config.SPEAKER_HISTORY_CAP
    from app.speaker import new_sample_id
    assert svc.id_fn is new_sample_id
```

In `brain/tests/test_voice.py`, replace `FakeSpeaker` (line ~552) with:

```python
class FakeSpeaker:
    """Fake SpeakerService: canned IdentifyOutcome, records identify AND
    record_history calls."""

    def __init__(self, result):  # (verified, score)
        verified, score = result
        self.result = speaker_mod.IdentifyOutcome(
            verified=verified, score=score, vec=[0.5, 0.5],
            adapted_sample_id=None)
        self.calls = []
        self.history = []

    def identify(self, user_id, pcm, device_hint, auth_is_kadir):
        self.calls.append((user_id, pcm, device_hint, auth_is_kadir))
        return self.result

    def record_history(self, user_id, **entry):
        self.history.append((user_id, entry))
        return "h1"
```

with `from app import speaker as speaker_mod` added to the file's imports. `ExplodingSpeaker` stays as is (no `record_history` — proves the bridge never calls it on the failure path). Then append the new bridge tests:

```python
@pytest.mark.asyncio
async def test_bridge_records_verification_history_after_the_speaker_event():
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None, speaker_service=speaker,
                                device_hint="headset", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._pump_events(fake_events(), ws)

    assert len(speaker.history) == 1
    user_id, entry = speaker.history[0]
    assert entry == {"score": 0.9, "verified": True, "vec": [0.5, 0.5],
                     "device_hint": "headset", "presence": "locked",
                     "trust_level": trust.MEDIUM, "adapted_sample_id": None}


class HistoryExplodingSpeaker(FakeSpeaker):
    """identify works, the history write blows up -- observability must never
    break the safety-relevant outputs (trust publish + client event)."""

    def record_history(self, user_id, **entry):
        raise RuntimeError("history write blew up")


@pytest.mark.asyncio
async def test_history_write_failure_is_logged_and_does_not_break_the_stream(caplog):
    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=HistoryExplodingSpeaker((True, 0.9)),
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    with caplog.at_level("ERROR"):
        await bridge._pump_events(fake_events(), ws)          # must not raise

    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": True, "score": 0.9})) in ws.sent
    assert voice_trust.peek(_trust_key()).trust_level == trust.MEDIUM
    assert "history record failed" in caplog.text


@pytest.mark.asyncio
async def test_no_history_row_when_identify_itself_failed():
    """The failure path has no embedding, so there is nothing a correction
    could later feed to the gallery -- no row is written (design decision,
    logged via the existing identify-failure log line)."""
    recorded = []

    class Exploding(ExplodingSpeaker):
        def record_history(self, user_id, **entry):
            recorded.append(entry)

    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("merhaba", finished=True))

    ws = FakeWS([])
    bridge = _armed(VoiceBridge(runner=None, session_service=None,
                                speaker_service=Exploding(),
                                device_hint="phone", presence="locked"))
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")
    await bridge._pump_events(fake_events(), ws)
    assert recorded == []
```

Append to `brain/tests/test_speaker_e2e.py` (inside/after the existing verified-utterance e2e flow — adapt to that file's harness names, asserting through the REAL `SpeakerService` so production wiring, not a fake, is what proves it):

```python
def test_e2e_verified_utterance_lands_in_the_verification_history():
    """Wiring proof at the outermost seam: after the WS-driven utterance the
    history doc must hold the fused presence/trust_level -- the fields only
    voice.py knows -- so bridge -> service -> store is pinned end to end."""
```

with body: run the existing harness flow, then `entries = speaker_history.load_history(db, user_email)`; assert `len(entries) == 1`, `entries[0]["verified"] is True`, `entries[0]["presence"] == "locked"`, `entries[0]["trust_level"] == trust.MEDIUM`, `entries[0]["vec"]` equals the harness's canned embed vector.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_speaker_service.py tests/test_voice.py tests/test_speaker_e2e.py -v`
Expected: new tests FAIL (`IdentifyOutcome` missing, `record_history` missing); old tuple-unpack tests still pass (they break in Step 3 — that ordering is fine).

- [ ] **Step 3: Implement**

`brain/app/speaker.py` — near the top (after `_normalize_sample`):

```python
from dataclasses import dataclass


@dataclass
class IdentifyOutcome:
    """One utterance's identity verdict plus what the verification history
    needs to make it correctable later (spec §4.2): the embedding itself and,
    if the utterance fed the gallery, the id of the sample it became."""
    verified: bool
    score: float
    vec: list[float]
    adapted_sample_id: str | None
```

`SpeakerService.__init__` gains `history_cap: int = 50` (stored as `self.history_cap`). `identify` becomes:

```python
    def identify(self, user_id: str, pcm: bytes, device_hint: str,
                 auth_is_kadir: bool) -> IdentifyOutcome:
        vec = self.embed_fn(pcm)
        adapted_sample_id = None
        with self._gallery_lock:
            profile = speaker_store.load_profile(self.db, user_id)
            score = profile.score(vec, self.top_k)
            anchor_score = profile.anchor_score(vec, self.top_k)
            verified = score >= self.accept
            if anchor_score >= self.adapt and auth_is_kadir:
                adapted_sample_id = profile.adapt(
                    vec, device_hint, self.cap, self.now_fn, self.id_fn)
                speaker_store.save_profile(self.db, user_id, profile)
```

(keep the ACCEPT/ADAPT comment block and the logging call — change the log's `adapted=%s` argument to `adapted_sample_id is not None` — and end with `return IdentifyOutcome(verified=verified, score=score, vec=vec, adapted_sample_id=adapted_sample_id)`). Add:

```python
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
```

with `from . import speaker_history` added next to the existing `from . import speaker_store`.

`brain/app/main.py` — `get_speaker_service` adds `history_cap=config.SPEAKER_HISTORY_CAP` to the constructor call.

`brain/app/voice.py` — in `_verify_utterance`, replace the identify/except block and add the history write after the client event:

```python
        try:
            # OFF THE EVENT LOOP: ... (keep the existing comment)
            outcome = await asyncio.to_thread(
                self.speaker_service.identify,
                self._user_id, pcm, self.device_hint, auth_is_kadir=True,
            )
            verified, score = outcome.verified, outcome.score
        except Exception:
            logging.exception("voice bridge: speaker.identify failed for %s", self._user_id)
            outcome, verified, score = None, False, 0.0
```

and after the existing `await ws.send_text(json.dumps(vp.evt_speaker(...)))` line:

```python
        # History AFTER the trust publish and the client event: those two are
        # the turn's safety-relevant outputs, the history row is observability
        # (spec §4.2) -- it must neither delay nor break them. No row on the
        # identify-failure path: there is no embedding a correction could feed
        # back, and the failure is already logged above.
        if outcome is not None:
            try:
                await asyncio.to_thread(
                    self.speaker_service.record_history, self._user_id,
                    score=outcome.score, verified=outcome.verified,
                    vec=outcome.vec, device_hint=self.device_hint,
                    presence=self.presence, trust_level=level,
                    adapted_sample_id=outcome.adapted_sample_id,
                )
            except Exception:
                logging.exception(
                    "voice bridge: history record failed for %s", self._user_id)
```

Update the OLD tuple-unpack call sites in `tests/test_speaker_service.py` (transformation, not weakening — every assert keeps its meaning):
`verified, score = _svc(db).identify(...)` → `out = _svc(db).identify(...)` with `out.verified` / `out.score`; `assert (verified, score) == (False, 0.0)` → `assert (out.verified, out.score) == (False, 0.0)`. Affected tests (all in that file): `test_matching_voice_verified`, `test_different_voice_not_verified`, `test_accepted_but_below_adapt_does_not_feed`, `test_no_adapt_when_not_authed_kadir`, `test_score_exactly_at_accept_is_verified`, `test_adapt_gate_ignores_adaptive_samples_so_poisoning_cannot_ratchet`, `test_empty_anchor_gallery_never_self_feeds`.

- [ ] **Step 4: Run the touched files, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_speaker_service.py tests/test_voice.py tests/test_speaker_e2e.py tests/test_enroll.py -v` → PASS.
Run: `.venv/bin/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Mutation-check the two load-bearing guards**

1. In `voice.py`, temporarily move the `record_history` block BEFORE `self._publish_trust` → `test_bridge_records_verification_history_after_the_speaker_event` must still pass but `test_history_write_failure_is_logged_and_does_not_break_the_stream` must FAIL if the publish/event asserts are ordered after a raise. (If it does not fail, the ordering claim is untested — tighten the test, don't drop the claim.)
2. In `main.get_speaker_service`, temporarily delete `history_cap=...` → `test_production_service_carries_the_config_history_cap` must FAIL.
Revert both mutations, re-run the two tests green.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/speaker.py brain/app/main.py brain/app/voice.py brain/tests/test_speaker_service.py brain/tests/test_voice.py brain/tests/test_speaker_e2e.py
git commit -m "feat(speaker): record every live verification into the history ring buffer

identify() now returns IdentifyOutcome (verified, score, vec,
adapted_sample_id); the bridge writes a spec-4.2 history entry off-loop
after publishing trust, never on the identify-failure path. 3d-1 complete:
schema + history land BEFORE enrollment (spec 3)."
```

**>>> 3d-1 burada biter — enrollment HITL'inin ön şartı kapanmıştır (spec §3). <<<**

---

### Task 4: `GET /api/voice/profile` (voice_manage router + kalite göstergeleri)

**Files:**
- Create: `brain/app/voice_manage.py`
- Modify: `brain/app/main.py` (import + `app.include_router(voice_manage.router)`)
- Modify: `brain/app/speaker.py` (`SpeakerService.overview`)
- Test: `brain/tests/test_voice_manage.py` (new)

**Interfaces:**
- Consumes: Task 1–3 schema/history; `require_user`; `main.get_speaker_service`.
- Produces:
  - `SpeakerService.overview(user_id) -> tuple[SpeakerProfile, list[dict]]` (one locked consistent read)
  - `voice_manage.router` mounted on `main.app`
  - `voice_manage.quality_indicators(entries, samples_by_id) -> dict` (pure)
  - `voice_manage._project(row, fields) -> dict`, `_SAMPLE_FIELDS`, `_HISTORY_FIELDS` (Tasks 5–7 reuse)
  - Response shape: `{"counts": {"anchors", "auto", "manual"}, "samples": [...], "history": [...], "quality": {...}}`

**Spec-open-point resolution (§6.1 "etiket kırılımlı ortalama"):** history rows carry no label of their own (spec §4.2 is exhaustive), so a row inherits the label of the gallery sample it adapted into (`adapted_sample_id` → sample.label). Rows that never fed the gallery contribute to `by_device` (every row has `device_hint`) but not `by_label`. This uses only spec'd schema; revisit after calibration if label coverage proves too thin.

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_voice_manage.py`:

```python
"""Dilim 3d spec §6: management endpoints. Fixture mirrors test_enroll.py's;
TestClient targets main.app so router MOUNTING is load-bearing (a forgotten
include_router turns every one of these into a 404)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app import speaker_history
from app.auth import require_user
from app.speaker import make_sample
from app.speaker_store import enroll_anchors, load_profile, save_profile
from tests.fakes import FakeDB

USER = "kadir@example.com"
A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]


@pytest.fixture
def manage_client(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: db, raising=False)
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    with TestClient(main_mod.app) as c:
        yield c, db
    main_mod.app.dependency_overrides.clear()


def _auth():
    main_mod.app.dependency_overrides[require_user] = lambda: USER


def _entry(i, *, score=0.8, verified=True, device="phone", adapted=None):
    return {"id": f"e{i}", "ts": f"t{i}", "score": score, "verified": verified,
            "device_hint": device, "presence": "locked", "trust_level": "MEDIUM",
            "adapted_sample_id": adapted, "correction": None, "vec": [0.1, 0.2]}


def _assert_no_vec(obj):
    """The spec §6 privacy claim, pinned recursively over the whole body."""
    if isinstance(obj, dict):
        assert "vec" not in obj
        for v in obj.values():
            _assert_no_vec(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_vec(v)


def test_profile_requires_auth(manage_client):
    c, _db = manage_client
    assert c.get("/api/voice/profile").status_code in (401, 403)


def test_empty_profile_has_the_full_shape(manage_client):
    c, _db = manage_client
    _auth()
    r = c.get("/api/voice/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"anchors": 0, "auto": 0, "manual": 0}
    assert body["samples"] == [] and body["history"] == []
    assert body["quality"]["mean_verified_score"] is None


def test_profile_reports_counts_samples_history_without_vectors(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], device_hint="phone",
                   now_fn=lambda: "t0", id_fn=lambda: "a1")
    profile = load_profile(db, USER)
    profile.adaptive.append(make_sample(B, "auto", "headset", "t1", "s1"))
    profile.adaptive.append(make_sample(B, "manual", "phone", "t2", "s2", label="hasta"))
    save_profile(db, USER, profile)
    speaker_history.record(db, USER, _entry(1, adapted="s1"), cap=50)

    body = c.get("/api/voice/profile").json()
    assert body["counts"] == {"anchors": 1, "auto": 1, "manual": 1}
    assert [s["id"] for s in body["samples"]] == ["a1", "s1", "s2"]
    assert body["samples"][2]["label"] == "hasta"
    assert [e["id"] for e in body["history"]] == ["e1"]
    assert body["history"][0]["trust_level"] == "MEDIUM"
    _assert_no_vec(body)


def test_profile_read_failure_is_a_turkish_502(manage_client, monkeypatch):
    c, _db = manage_client
    _auth()
    import app.speaker_store as store_mod

    def boom(db_, user_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(store_mod, "load_profile", boom)
    r = c.get("/api/voice/profile")
    assert r.status_code == 502
    assert "altyapı" in r.json()["detail"]


# --- quality_indicators: pure, unit-tested without HTTP ----------------------


def test_quality_mean_fail_rate_and_device_breakdown():
    from app.voice_manage import quality_indicators
    entries = [_entry(1, score=0.6), _entry(2, score=0.8),
               _entry(3, score=0.2, verified=False, device="headset")]
    q = quality_indicators(entries, {})
    assert q["mean_verified_score"] == pytest.approx(0.7)
    assert q["fail_rate"] == pytest.approx(1 / 3)
    assert q["by_device"]["phone"] == pytest.approx(0.7)
    assert q["by_device"]["headset"] == pytest.approx(0.2)


def test_quality_label_breakdown_follows_the_adapted_sample(manage_client):
    from app.voice_manage import quality_indicators
    samples = {"s1": make_sample(A, "auto", "phone", "t", "s1", label="gurultulu")}
    entries = [_entry(1, score=0.4, adapted="s1"), _entry(2, score=0.9)]
    q = quality_indicators(entries, samples)
    assert q["by_label"] == {"gurultulu": pytest.approx(0.4)}


def test_quality_trend_compares_last_10_with_previous_10():
    from app.voice_manage import quality_indicators
    entries = [_entry(i, score=0.2) for i in range(10)] + \
              [_entry(10 + i, score=0.8) for i in range(10)]
    q = quality_indicators(entries, {})
    assert q["trend"] == {"last10": pytest.approx(0.8),
                          "previous10": pytest.approx(0.2)}


def test_quality_of_empty_history_is_all_none():
    from app.voice_manage import quality_indicators
    q = quality_indicators([], {})
    assert q["mean_verified_score"] is None and q["fail_rate"] is None
    assert q["by_device"] == {} and q["by_label"] == {}
    assert q["trend"] == {"last10": None, "previous10": None}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.voice_manage'`.

- [ ] **Step 3: Implement**

`brain/app/speaker.py` — add to `SpeakerService`:

```python
    def overview(self, user_id: str) -> tuple:
        """One CONSISTENT read of gallery + history for GET /api/voice/profile:
        under the lock so a concurrent adapt/correction cannot land between the
        two loads and show a history row pointing at a sample that "does not
        exist yet" (spec §10)."""
        with self._gallery_lock:
            return (speaker_store.load_profile(self.db, user_id),
                    speaker_history.load_history(self.db, user_id))
```

Create `brain/app/voice_manage.py`:

```python
"""Speaker identity management API (Katman 2b Dilim 3d, spec §6): visibility,
correction, deletion. Every endpoint is require_user-gated and keyed by the
authenticated user; every gallery/history touch goes through SpeakerService
(the ONE gallery lock) and runs OFF the event loop via asyncio.to_thread --
this process serves /api/chat and /ws/voice from that same loop (spec §10).

Embedding vectors NEVER leave the server (spec §6): responses are built by
explicit allowlist projection, and tests pin the absence of "vec" anywhere in
any response body. There is deliberately NO "biometric gate passed" header
anywhere in this API -- an unverifiable client assertion is not a signal
(spec §7, same class as `presence`)."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_user

router = APIRouter()

_SAMPLE_FIELDS = ("id", "source", "ts", "device_hint", "label", "note")
_HISTORY_FIELDS = ("id", "ts", "score", "verified", "device_hint", "presence",
                   "trust_level", "adapted_sample_id", "correction")

_INFRA_502 = "İşlem şu anda yapılamıyor (altyapı hatası). Az sonra tekrar dene."


def _project(row: dict, fields: tuple) -> dict:
    """Allowlist projection: `vec` (or any future private field) cannot leak
    by being FORGOTTEN -- only named fields ever cross into a response."""
    return {f: row.get(f) for f in fields}


def _service():
    from . import main   # runtime import: main mounts this module's router
    return main.get_speaker_service()


def _mean(xs: list[float]):
    return sum(xs) / len(xs) if xs else None


def quality_indicators(entries: list[dict], samples_by_id: dict) -> dict:
    """Interpreted read of the raw history (spec §6.1): rolling mean of
    verified scores, failure rate, per-device means, label-linked means (a row
    inherits the label of the gallery sample it adapted into -- history rows
    carry no label of their own, spec §4.2), and last-10 vs previous-10."""
    scores = [e["score"] for e in entries]
    verified_scores = [e["score"] for e in entries if e["verified"]]
    by_device: dict[str, list[float]] = {}
    by_label: dict[str, list[float]] = {}
    for e in entries:
        by_device.setdefault(e.get("device_hint", "unknown"), []).append(e["score"])
        label = (samples_by_id.get(e.get("adapted_sample_id") or "") or {}).get("label")
        if label:
            by_label.setdefault(label, []).append(e["score"])
    return {
        "mean_verified_score": _mean(verified_scores),
        "fail_rate": (len(entries) - len(verified_scores)) / len(entries) if entries else None,
        "by_device": {k: _mean(v) for k, v in by_device.items()},
        "by_label": {k: _mean(v) for k, v in by_label.items()},
        "trend": {"last10": _mean(scores[-10:]),
                  "previous10": _mean(scores[-20:-10])},
    }


@router.get("/api/voice/profile")
async def get_profile(email: str = Depends(require_user)):
    try:
        profile, history = await asyncio.to_thread(lambda: _service().overview(email))
    except Exception:
        logging.exception("voice_manage: profile read failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    samples = profile.anchors + profile.adaptive
    samples_by_id = {s["id"]: s for s in samples}
    return {
        "counts": {
            "anchors": len(profile.anchors),
            "auto": sum(1 for s in profile.adaptive if s["source"] == "auto"),
            "manual": sum(1 for s in profile.adaptive if s["source"] == "manual"),
        },
        "samples": [_project(s, _SAMPLE_FIELDS) for s in samples],
        "history": [_project(e, _HISTORY_FIELDS) for e in history],
        "quality": quality_indicators(history, samples_by_id),
    }
```

`brain/app/main.py` — extend the package import line to `from . import config, messages, speaker, voice, voice_manage, voice_trust` and add `app.include_router(voice_manage.router)` right under `app.include_router(voice.router)`.

- [ ] **Step 4: Run tests, then full suite**

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py -v` → PASS.
Run: `.venv/bin/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Mutation-check the privacy projection**

Temporarily add `"vec"` to `_HISTORY_FIELDS` → `test_profile_reports_counts_samples_history_without_vectors` must FAIL. Revert, re-run green. (This proves the recursive scan actually reaches history rows.)

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/voice_manage.py brain/app/speaker.py brain/app/main.py brain/tests/test_voice_manage.py
git commit -m "feat(voice-manage): GET /api/voice/profile with derived quality indicators

Gallery summary + full history (vector-free by allowlist projection) +
mean/fail-rate/device/label breakdowns and last10-vs-previous10 trend
(Dilim 3d spec 6, 6.1)."
```

---

### Task 5: `PATCH /api/voice/sample/{id}` + `DELETE /api/voice/sample/{id}`

**Files:**
- Modify: `brain/app/speaker.py` (exceptions; `SpeakerService.__init__` gains `labels`; `update_sample`, `delete_sample`)
- Modify: `brain/app/main.py` (`get_speaker_service` passes `labels=config.SPEAKER_SAMPLE_LABELS`)
- Modify: `brain/app/voice_manage.py` (two endpoints)
- Test: `brain/tests/test_voice_manage.py`, `brain/tests/test_speaker_service.py`

**Interfaces:**
- Consumes: Task 4's `_project`/`_SAMPLE_FIELDS`/`_INFRA_502`/`_service`; Task 1 schema.
- Produces:
  - `speaker.SampleNotFound(KeyError)` — endpoint maps to 404
  - `speaker.RuleViolation(ValueError)` — carries the Turkish message, endpoint maps to 400
  - `SpeakerService.__init__(..., labels: frozenset = frozenset())`
  - `SpeakerService.update_sample(user_id, sample_id, *, label=_UNSET, note=_UNSET) -> dict`
  - `SpeakerService.delete_sample(user_id, sample_id) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `brain/tests/test_voice_manage.py`:

```python
# --- PATCH/DELETE /api/voice/sample/{id} (spec §6, §8) ----------------------


def _seed_profile(db):
    """2 anchors + 1 auto sample with known ids."""
    ids = iter(["a1", "a2"])
    enroll_anchors(db, USER, [A, B], device_hint="phone",
                   now_fn=lambda: "t0", id_fn=lambda: next(ids))
    profile = load_profile(db, USER)
    profile.adaptive.append(make_sample(B, "auto", "headset", "t1", "s1"))
    save_profile(db, USER, profile)


def test_patch_sample_updates_label_and_note(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.patch("/api/voice/sample/s1", json={"label": "gurultulu", "note": "metroda"})
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "gurultulu" and body["note"] == "metroda"
    _assert_no_vec(body)
    stored = load_profile(db, USER).adaptive[0]
    assert stored["label"] == "gurultulu" and stored["note"] == "metroda"


def test_patch_note_only_preserves_the_label(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    c.patch("/api/voice/sample/s1", json={"label": "hasta"})
    r = c.patch("/api/voice/sample/s1", json={"note": "sadece not"})
    assert r.status_code == 200
    assert r.json()["label"] == "hasta"          # omitted field untouched


def test_patch_label_null_clears_it(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    c.patch("/api/voice/sample/s1", json={"label": "hasta"})
    r = c.patch("/api/voice/sample/s1", json={"label": None})
    assert r.status_code == 200 and r.json()["label"] is None


def test_patch_rejects_a_label_outside_the_fixed_set(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.patch("/api/voice/sample/s1", json={"label": "nezleli"})
    assert r.status_code == 400
    assert "Geçersiz etiket" in r.json()["detail"]
    assert load_profile(db, USER).adaptive[0]["label"] is None


def test_patch_unknown_sample_is_404(manage_client):
    c, _db = manage_client
    _auth()
    r = c.patch("/api/voice/sample/yok", json={"label": "hasta"})
    assert r.status_code == 404


def test_delete_adaptive_sample(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.delete("/api/voice/sample/s1")
    assert r.status_code == 200 and r.json() == {"deleted": "s1"}
    assert load_profile(db, USER).adaptive == []


def test_delete_an_anchor_when_others_remain(manage_client):
    c, db = manage_client
    _auth()
    _seed_profile(db)
    r = c.delete("/api/voice/sample/a1")
    assert r.status_code == 200
    assert [s["id"] for s in load_profile(db, USER).anchors] == ["a2"]


def test_the_last_anchor_cannot_be_deleted(manage_client):
    """spec §8: an anchorless profile cannot score ACCEPT and leaves ADAPT
    refereeing without a reference -- an explicit 400 beats a silently
    non-functional profile."""
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], device_hint="phone",
                   now_fn=lambda: "t0", id_fn=lambda: "a1")
    r = c.delete("/api/voice/sample/a1")
    assert r.status_code == 400
    assert "Son çapa" in r.json()["detail"]
    assert len(load_profile(db, USER).anchors) == 1


def test_delete_unknown_sample_is_404(manage_client):
    c, _db = manage_client
    _auth()
    assert c.delete("/api/voice/sample/yok").status_code == 404


def test_sample_endpoints_require_auth(manage_client):
    c, _db = manage_client
    assert c.patch("/api/voice/sample/s1", json={"label": "hasta"}).status_code in (401, 403)
    assert c.delete("/api/voice/sample/s1").status_code in (401, 403)
```

Append to `brain/tests/test_speaker_service.py` (the §10 lock rule, `watching_save` pattern):

```python
def test_update_sample_holds_the_gallery_lock_across_its_save(monkeypatch):
    from app import speaker as speaker_mod
    db = FakeDB(); enroll_anchors(db, "k", [A], id_fn=lambda: "a1")
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t",
                         accept=0.9, adapt=0.97, cap=5, top_k=1,
                         labels=frozenset({"hasta"}))
    observed = {}
    real_save = speaker_mod.speaker_store.save_profile

    def watching_save(db_, user_id, profile):
        observed["locked"] = svc._gallery_lock.locked()
        return real_save(db_, user_id, profile)

    monkeypatch.setattr(speaker_mod.speaker_store, "save_profile", watching_save)
    svc.update_sample("k", "a1", label="hasta")
    assert observed["locked"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py tests/test_speaker_service.py -v`
Expected: new tests FAIL (405/404 from missing routes, `update_sample` missing).

- [ ] **Step 3: Implement**

`brain/app/speaker.py` — near `IdentifyOutcome`:

```python
class SampleNotFound(KeyError):
    """Requested sample/history id does not exist (endpoint maps to 404 --
    it may have been deleted, evicted, or dropped off the ring buffer)."""


class RuleViolation(ValueError):
    """A management rule refused the operation (endpoint maps to 400).
    Carries the Turkish user-facing message (spec §10)."""


_UNSET = object()
```

`SpeakerService.__init__` gains `labels: frozenset = frozenset()` (stored as `self.labels`). Add methods:

```python
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
```

`brain/app/main.py` — `get_speaker_service` adds `labels=config.SPEAKER_SAMPLE_LABELS`.

`brain/app/voice_manage.py` — add (plus `from pydantic import BaseModel` and `from . import speaker` to imports):

```python
class SamplePatch(BaseModel):
    label: str | None = None
    note: str | None = None


@router.patch("/api/voice/sample/{sample_id}")
async def patch_sample(sample_id: str, req: SamplePatch,
                       email: str = Depends(require_user)):
    # model_fields_set distinguishes "absent" from an explicit null: label=None
    # must CLEAR the label, an omitted label must not touch it.
    kwargs = {}
    if "label" in req.model_fields_set:
        kwargs["label"] = req.label
    if "note" in req.model_fields_set:
        kwargs["note"] = req.note
    try:
        sample = await asyncio.to_thread(
            lambda: _service().update_sample(email, sample_id, **kwargs))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404, detail="Örnek bulunamadı")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: patch failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    return _project(sample, _SAMPLE_FIELDS)


@router.delete("/api/voice/sample/{sample_id}")
async def delete_sample(sample_id: str, email: str = Depends(require_user)):
    try:
        await asyncio.to_thread(lambda: _service().delete_sample(email, sample_id))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404, detail="Örnek bulunamadı")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: sample delete failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    return {"deleted": sample_id}
```

- [ ] **Step 4: Run tests, then full suite**

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py tests/test_speaker_service.py -v` → PASS.
Run: `.venv/bin/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/speaker.py brain/app/main.py brain/app/voice_manage.py brain/tests/test_voice_manage.py brain/tests/test_speaker_service.py
git commit -m "feat(voice-manage): sample label/note editing and single-sample deletion

PATCH validates against the fixed label set; DELETE covers anchors but
refuses the last one with an explicit Turkish 400 (Dilim 3d spec 6, 8)."
```

---

### Task 6: Düzeltme uçları — `confirm` / `reject` (+ manual cap)

**Files:**
- Modify: `brain/app/speaker.py` (`SpeakerService.__init__` gains `manual_cap`; `_find_entry`, `confirm_history`, `reject_history`)
- Modify: `brain/app/main.py` (`get_speaker_service` passes `manual_cap=config.SPEAKER_MANUAL_CAP`)
- Modify: `brain/app/voice_manage.py` (two endpoints)
- Test: `brain/tests/test_voice_manage.py`, `brain/tests/test_speaker_service.py`

**Interfaces:**
- Consumes: Tasks 1–5 (`make_sample`, `speaker_history.save_history`, `SampleNotFound`, `RuleViolation`, `_project`).
- Produces:
  - `SpeakerService.__init__(..., manual_cap: int = 5)`
  - `SpeakerService.confirm_history(user_id, entry_id) -> dict` (`{"added_sample_id", "already"}`)
  - `SpeakerService.reject_history(user_id, entry_id) -> dict` (`{"removed_sample_id", "already"}`)

- [ ] **Step 1: Write the failing service-level tests**

Append to `brain/tests/test_speaker_service.py`:

```python
# --- Dilim 3d spec §5+§6: corrections --------------------------------------

from app import speaker_history


def _hist_entry(i, vec, *, adapted=None, correction=None):
    return {"id": f"e{i}", "ts": f"t{i}", "score": 0.5, "verified": True,
            "device_hint": "phone", "presence": "locked", "trust_level": "MEDIUM",
            "adapted_sample_id": adapted, "correction": correction, "vec": vec}


def _mgmt_svc(db, manual_cap=5):
    ids = iter(f"id{i}" for i in range(100))
    return SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "now",
                         accept=0.9, adapt=0.97, cap=5, top_k=1,
                         id_fn=lambda: next(ids), manual_cap=manual_cap)


def test_confirm_adds_a_manual_sample_and_marks_the_entry():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    result = svc.confirm_history("k", "e1")
    assert result["already"] is False
    profile = load_profile(db, "k")
    manuals = [s for s in profile.adaptive if s["source"] == "manual"]
    assert len(manuals) == 1 and manuals[0]["vec"] == FAR
    assert manuals[0]["id"] == result["added_sample_id"]
    assert profile.anchors[0]["source"] == "enroll", "manual must NEVER become an anchor"
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] == "confirmed"
    assert entry["adapted_sample_id"] == result["added_sample_id"]


def test_confirm_is_idempotent_and_does_not_burn_the_cap():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    first = svc.confirm_history("k", "e1")
    second = svc.confirm_history("k", "e1")
    assert second["already"] is True
    assert second["added_sample_id"] == first["added_sample_id"]
    profile = load_profile(db, "k")
    assert sum(1 for s in profile.adaptive if s["source"] == "manual") == 1


def test_the_sixth_manual_sample_is_refused_with_the_count():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    from app.speaker import make_sample
    profile = load_profile(db, "k")
    for i in range(5):
        profile.adaptive.append(make_sample(FAR, "manual", "phone", "t", f"m{i}"))
    from app.speaker_store import save_profile
    save_profile(db, "k", profile)
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    import pytest as _pytest
    from app.speaker import RuleViolation
    with _pytest.raises(RuleViolation, match="5/5"):
        svc.confirm_history("k", "e1")
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] is None, "a refused confirm must not mark the entry"


def test_reject_removes_the_adapted_sample_and_marks_the_entry():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    svc = _mgmt_svc(db)
    out = svc.identify("k", b"A", "phone", auth_is_kadir=True)   # adapts (score 1.0)
    assert out.adapted_sample_id is not None
    # identify() recorded nothing (that is voice.py's job) -- seed the entry:
    speaker_history.record(db, "k", _hist_entry(1, A, adapted=out.adapted_sample_id), cap=50)
    result = svc.reject_history("k", "e1")
    assert result["removed_sample_id"] == out.adapted_sample_id
    assert load_profile(db, "k").adaptive == []
    entry = speaker_history.load_history(db, "k")[0]
    assert entry["correction"] == "rejected" and entry["adapted_sample_id"] is None


def test_reject_of_a_never_adapted_entry_just_marks_it():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    result = svc.reject_history("k", "e1")
    assert result == {"removed_sample_id": None, "already": False}
    assert speaker_history.load_history(db, "k")[0]["correction"] == "rejected"


def test_reject_is_idempotent():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    svc.reject_history("k", "e1")
    assert svc.reject_history("k", "e1")["already"] is True


def test_mind_can_be_changed_in_both_directions():
    """spec §6: reversing is legitimate use. confirm -> reject removes the
    manual sample; reject -> confirm adds a fresh one."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    added = svc.confirm_history("k", "e1")["added_sample_id"]
    removed = svc.reject_history("k", "e1")["removed_sample_id"]
    assert removed == added
    assert [s for s in load_profile(db, "k").adaptive if s["source"] == "manual"] == []
    re_added = svc.confirm_history("k", "e1")["added_sample_id"]
    assert re_added is not None and re_added != added
    manuals = [s for s in load_profile(db, "k").adaptive if s["source"] == "manual"]
    assert [s["id"] for s in manuals] == [re_added]


def test_unknown_history_entry_raises_not_found():
    import pytest as _pytest
    from app.speaker import SampleNotFound
    db = FakeDB()
    svc = _mgmt_svc(db)
    with _pytest.raises(SampleNotFound):
        svc.confirm_history("k", "yok")
    with _pytest.raises(SampleNotFound):
        svc.reject_history("k", "yok")


def test_manual_sample_votes_in_accept_but_never_referees_adapt():
    """spec §5's core split, mutation-verified by construction: the probe F is
    FAR from the anchor A but IDENTICAL to the manual sample, so
    - ACCEPT (full gallery, top_k=1) scores 1.0 -> verified: manual VOTES;
    - ADAPT (anchors only) scores 0.0 -> no self-feed: manual cannot REFEREE.
    If anchor_score ever read the full gallery, the auto sample added here
    would prove it (adaptive would grow)."""
    db = FakeDB(); enroll_anchors(db, "k", [A])
    speaker_history.record(db, "k", _hist_entry(1, FAR), cap=50)
    svc = _mgmt_svc(db)
    svc.confirm_history("k", "e1")                    # manual sample = FAR
    svc2 = SpeakerService(db, embed_fn=lambda pcm: FAR, now_fn=lambda: "t",
                          accept=0.9, adapt=0.9, cap=5, top_k=1)
    out = svc2.identify("k", b"F", "phone", auth_is_kadir=True)
    assert out.verified is True and out.score == 1.0          # manual voted
    assert out.adapted_sample_id is None                       # ...but did not referee
    profile = load_profile(db, "k")
    assert sum(1 for s in profile.adaptive if s["source"] == "auto") == 0
```

- [ ] **Step 2: Write the failing endpoint tests**

Append to `brain/tests/test_voice_manage.py`:

```python
# --- POST /api/voice/history/{id}/confirm + /reject (spec §6) ---------------


def _seed_history(db, *, adapted=None, correction=None):
    speaker_history.record(db, USER, {
        "id": "e1", "ts": "t1", "score": 0.5, "verified": True,
        "device_hint": "phone", "presence": "locked", "trust_level": "MEDIUM",
        "adapted_sample_id": adapted, "correction": correction, "vec": B,
    }, cap=50)


def test_confirm_endpoint_adds_a_manual_sample(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    _seed_history(db)
    r = c.post("/api/voice/history/e1/confirm")
    assert r.status_code == 200
    body = r.json()
    assert body["already"] is False and body["added_sample_id"]
    _assert_no_vec(body)
    manuals = [s for s in load_profile(db, USER).adaptive if s["source"] == "manual"]
    assert len(manuals) == 1


def test_reject_endpoint_marks_the_entry(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    _seed_history(db)
    r = c.post("/api/voice/history/e1/reject")
    assert r.status_code == 200 and r.json()["already"] is False
    assert speaker_history.load_history(db, USER)[0]["correction"] == "rejected"


def test_correction_of_a_missing_entry_is_404(manage_client):
    c, _db = manage_client
    _auth()
    r = c.post("/api/voice/history/yok/confirm")
    assert r.status_code == 404
    assert "artık yok" in r.json()["detail"]


def test_manual_cap_surfaces_as_a_400_with_the_count(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    profile = load_profile(db, USER)
    for i in range(5):
        profile.adaptive.append(make_sample(B, "manual", "phone", "t", f"m{i}"))
    save_profile(db, USER, profile)
    _seed_history(db)
    r = c.post("/api/voice/history/e1/confirm")
    assert r.status_code == 400 and "5/5" in r.json()["detail"]


def test_correction_endpoints_require_auth(manage_client):
    c, _db = manage_client
    assert c.post("/api/voice/history/e1/confirm").status_code in (401, 403)
    assert c.post("/api/voice/history/e1/reject").status_code in (401, 403)


def test_production_service_carries_the_config_manual_cap_and_labels(manage_client, monkeypatch):
    """Same wiring-guard class as Task 3's history_cap test."""
    from app import config
    svc = main_mod.get_speaker_service()
    assert svc.manual_cap == config.SPEAKER_MANUAL_CAP
    assert svc.labels == config.SPEAKER_SAMPLE_LABELS
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_speaker_service.py tests/test_voice_manage.py -v`
Expected: new tests FAIL (`confirm_history` missing, routes 404).

- [ ] **Step 4: Implement**

`brain/app/speaker.py` — `SpeakerService.__init__` gains `manual_cap: int = 5` (stored). Add:

```python
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
```

`brain/app/main.py` — `get_speaker_service` adds `manual_cap=config.SPEAKER_MANUAL_CAP`.

`brain/app/voice_manage.py` — add:

```python
@router.post("/api/voice/history/{entry_id}/confirm")
async def confirm_history(entry_id: str, email: str = Depends(require_user)):
    try:
        return await asyncio.to_thread(
            lambda: _service().confirm_history(email, entry_id))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404,
                            detail="Geçmiş kaydı artık yok (silinmiş ya da tampondan düşmüş olabilir)")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: confirm failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)


@router.post("/api/voice/history/{entry_id}/reject")
async def reject_history(entry_id: str, email: str = Depends(require_user)):
    try:
        return await asyncio.to_thread(
            lambda: _service().reject_history(email, entry_id))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404,
                            detail="Geçmiş kaydı artık yok (silinmiş ya da tampondan düşmüş olabilir)")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: reject failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
```

- [ ] **Step 5: Run tests, then full suite; mutation-check the §5 split**

Run: `.venv/bin/python -m pytest tests/test_speaker_service.py tests/test_voice_manage.py -v` → PASS.
Run: `.venv/bin/python -m pytest tests -q` → all pass.
Mutation: in `SpeakerProfile.anchor_score`, temporarily score against `self.all_vectors()` → `test_manual_sample_votes_in_accept_but_never_referees_adapt` must FAIL (adaptive grows). Revert, re-run green.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/speaker.py brain/app/main.py brain/app/voice_manage.py brain/tests/test_speaker_service.py brain/tests/test_voice_manage.py
git commit -m "feat(voice-manage): human corrections -- confirm/reject with manual cap

'Bu bendim' turns the stored embedding into a manual sample (votes in
ACCEPT, never referees ADAPT); 'ben degildim' removes the linked sample.
Both idempotent, reversals allowed, cap 5 refused with the count
(Dilim 3d spec 5, 6)."
```

---

### Task 7: `DELETE /api/voice/profile` — profil + geçmiş birlikte

**Files:**
- Modify: `brain/app/speaker.py` (`SpeakerService.delete_profile_and_history`)
- Modify: `brain/app/voice_manage.py` (endpoint)
- Test: `brain/tests/test_voice_manage.py`

**Interfaces:**
- Consumes: Task 2's `speaker_store.delete_profile` + `speaker_history.delete_history`.
- Produces: `SpeakerService.delete_profile_and_history(user_id) -> None`; `DELETE /api/voice/profile` → `{"deleted": true}`.

- [ ] **Step 1: Write the failing tests**

Append to `brain/tests/test_voice_manage.py`:

```python
# --- DELETE /api/voice/profile (spec §8: no half-deletion) ------------------


def test_profile_deletion_removes_profile_AND_history(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A], id_fn=lambda: "a1")
    _seed_history(db)
    r = c.delete("/api/voice/profile")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert db.collection("speaker_profiles").document(USER).get().exists is False
    assert db.collection("speaker_history").document(USER).get().exists is False


def test_profile_deletion_leaves_other_users_alone(manage_client):
    c, db = manage_client
    _auth()
    enroll_anchors(db, USER, [A])
    enroll_anchors(db, "baskasi@example.com", [B])
    c.delete("/api/voice/profile")
    assert len(load_profile(db, "baskasi@example.com").anchors) == 1


def test_profile_deletion_requires_auth(manage_client):
    c, _db = manage_client
    assert c.delete("/api/voice/profile").status_code in (401, 403)


def test_profile_deletion_failure_is_a_turkish_502(manage_client, monkeypatch):
    c, db = manage_client
    _auth()
    import app.speaker_store as store_mod

    def boom(db_, user_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(store_mod, "delete_profile", boom)
    r = c.delete("/api/voice/profile")
    assert r.status_code == 502 and "altyapı" in r.json()["detail"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py -v`
Expected: new tests FAIL (405 — no DELETE route on /api/voice/profile).

- [ ] **Step 3: Implement**

`brain/app/speaker.py`:

```python
    def delete_profile_and_history(self, user_id: str) -> None:
        """spec §8: no half-deletion -- the gallery and the verification
        history go together, under the lock so a concurrent identify cannot
        resurrect a partial write in between."""
        with self._gallery_lock:
            speaker_store.delete_profile(self.db, user_id)
            speaker_history.delete_history(self.db, user_id)
```

`brain/app/voice_manage.py`:

```python
@router.delete("/api/voice/profile")
async def delete_profile(email: str = Depends(require_user)):
    try:
        await asyncio.to_thread(lambda: _service().delete_profile_and_history(email))
    except Exception:
        logging.exception("voice_manage: profile delete failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    logging.info("voice_manage: profile+history deleted for user_id=%s", email)
    return {"deleted": True}
```

- [ ] **Step 4: Run tests, then full suite**

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py -v` → PASS.
Run: `.venv/bin/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/app/speaker.py brain/app/voice_manage.py brain/tests/test_voice_manage.py
git commit -m "feat(voice-manage): whole-profile deletion takes the history with it

DELETE /api/voice/profile removes speaker_profiles AND speaker_history
together under the gallery lock -- no half-deletion (Dilim 3d spec 8)."
```

---

### Task 8: Eşzamanlılık kanıtı, kapanış temizliği, dokümantasyon

**Files:**
- Test: `brain/tests/test_voice_manage.py` (concurrency)
- Modify: `brain/README.md` (speaker section: endpoints, history, caps)
- Modify: `docs/superpowers/specs/2026-07-24-katman-2b-dilim3d-ses-kimligi-yonetimi-design.md` (Durum satırı)

**Interfaces:** consumes everything above; produces nothing new — this is the gate before review.

- [ ] **Step 1: Write the failing/proving concurrency test**

Append to `brain/tests/test_voice_manage.py`:

```python
# --- spec §11: correction vs live identify, no lost writes ------------------


def test_concurrent_confirm_and_identify_do_not_lose_a_write():
    """The race the shared lock exists for, at the two mutation entry points
    Dilim 3d adds: a live identify() that adapts and a confirm_history() from
    the management surface interleave from two threads -- BOTH new samples
    must survive (same barrier pattern as test_enroll.py's enroll/identify
    race)."""
    import threading

    from app.speaker import SpeakerService

    db = FakeDB()
    ids = iter(f"id{i}" for i in range(10))
    svc = SpeakerService(db, embed_fn=lambda pcm: A, now_fn=lambda: "t",
                         accept=0.35, adapt=0.6, cap=20, top_k=3,
                         id_fn=lambda: next(ids), manual_cap=5, history_cap=50)
    svc.enroll(USER, [A])
    _seed_history(db)

    start = threading.Barrier(2)
    errors = []

    def do_confirm():
        try:
            start.wait(timeout=5)
            svc.confirm_history(USER, "e1")
        except Exception as exc:            # pragma: no cover - reported below
            errors.append(exc)

    def do_identify():
        try:
            start.wait(timeout=5)
            svc.identify(USER, b"\x00\x01", "phone", auth_is_kadir=True)
        except Exception as exc:            # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=do_confirm), threading.Thread(target=do_identify)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, errors
    profile = load_profile(db, USER)
    assert sum(1 for s in profile.adaptive if s["source"] == "manual") == 1, \
        "the confirmed manual sample was lost to the adapt write"
    assert sum(1 for s in profile.adaptive if s["source"] == "auto") == 1, \
        "the adaptive sample was lost to the confirm write"
```

Run: `.venv/bin/python -m pytest tests/test_voice_manage.py::test_concurrent_confirm_and_identify_do_not_lose_a_write -v` → PASS (the lock already exists; this pins it at the new entry points). Sanity-check it is load-bearing: temporarily replace `confirm_history`'s `with self._gallery_lock:` with `if True:` and run the test ~5 times — at least one run must fail; revert.

- [ ] **Step 2: README + spec status**

`brain/README.md` speaker section: add a "Ses kimliği yönetimi (Dilim 3d)" subsection — endpoint table (the six routes + one-line each), the history model (ring buffer 50, embeddings-not-audio, corrections), env vars `JARVIS_SPEAKER_HISTORY_CAP` / `JARVIS_SPEAKER_MANUAL_CAP`, the label set, and the two privacy claims verbatim: vectors never leave the server; no "biometri yaptım" header exists (client biometric gate is Android-side UX, the server never trusts it — spec §7).

Spec file: change `**Durum:** tasarım onaylandı, plan yazılacak` → `**Durum:** plan yazıldı (docs/superpowers/plans/2026-07-24-katman-2b-dilim3d-ses-kimligi-yonetimi-server.md), implementation sürüyor/bitti`.

- [ ] **Step 3: Full suites, BOTH interpreters**

Run: `cd /home/user/Projeler/JARVIS/brain && .venv/bin/python -m pytest tests -q` → all pass.
Run: `.venv-speaker/bin/python -m pytest tests -q` → all pass (torch path unaffected, but this is the interpreter prod ships with — it must stay green).

- [ ] **Step 4: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add brain/tests/test_voice_manage.py brain/README.md docs/superpowers/specs/2026-07-24-katman-2b-dilim3d-ses-kimligi-yonetimi-design.md
git commit -m "test(voice-manage): pin correction-vs-identify concurrency; document 3d

Barrier-driven confirm/identify race proves the shared gallery lock at
the new mutation entry points; README documents the management API,
history model, caps and privacy claims."
```

---

## Self-Review (yapıldı)

- **Spec coverage:** §3 sıralama → Task 1–3 önce, banner ile işaretli; §4.1 → Task 1; §4.2 → Task 2–3; §5 → Task 1 (eviction/cap) + Task 6 (oy/hakem ayrımı, cap 5); §6 altı uç → Task 4–7; §6.1 → Task 4 (etiket kırılımı açık-uç çözümü Task 4 başında gerekçeli); §7 → sunucu tarafı "başlık gönderilmez" (voice_manage docstring + README, Android kapısı 3d-3 planında); §8 → Task 5 (son çapa) + Task 7; §10 → her uçta Türkçe 400/404/502 + lock + to_thread; §11 test stratejisi maddelerinin tamamı birebir karşılandı (şema, yetki ayrımı mutasyonlu, cap, çapa koruması, silme bütünlüğü, halka tamponu, vektör sızıntısı, eşzamanlılık); §12 açık uçlar → env-ayarlanabilir cap'ler, etiket seti config'de tek yerde.
- **Placeholder scan:** temiz — her adımda gerçek kod/komut var; tek esneklik Task 3'ün e2e gövdesi (mevcut harness'ın adlarına uyarlanacak, assert listesi verildi).
- **Type consistency:** `IdentifyOutcome` alanları, `record_history` kwargs'ı, `_project` alan listeleri ve exception adları görevler arası birebir aynı; `id_fn`/`now_fn`/`labels`/`manual_cap`/`history_cap` ctor paramları Task 3/5/6 wiring testleriyle çivili.
