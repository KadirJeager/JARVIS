# Katman 2b Dilim 3a — Kadir Ses-Kimliği Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `jarvis-voice`'a, ham PCM ses akışında Kadir'i sesinden doğrulayan, kendini besleyen (adaptive) ve kimliği politika katmanına besleyen sunucu-tarafı bir ses-kimliği çekirdeği eklemek.

**Architecture:** İstemci-agnostik `/ws/voice` sözleşmesi üzerinde çalışan in-process modüller: `speaker.py` (ECAPA-TDNN embedding + adaptive galeri), `trust.py` (risk-tabanlı güven füzyonu), `speaker_store` (Firestore kalıcı voiceprint), politika modülasyonu ([policy.py](../../../brain/app/policy.py)) ve voice bridge entegrasyonu ([voice.py](../../../brain/app/voice.py)). Torch-bağımsız saf mantık (trust, galeri, politika, store) mevcut 3.14 venv'de; torch-bağımlı embedding üretim Python'u (3.12) ile hizalı test edilir. Doğrulama scriptli WS harness'ı + gerçek ses fixture'larıyla.

**Tech Stack:** Python 3.12 (prod, [Dockerfile](../../../brain/Dockerfile)), FastAPI, google-adk 1.36.2, google-cloud-firestore, **speechbrain (`inference` API) + torch + torchaudio** (yeni opsiyonel dep), pytest + pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-07-24-katman-2b-dilim3a-ses-kimligi-design.md](../specs/2026-07-24-katman-2b-dilim3a-ses-kimligi-design.md)

## Global Constraints

- **Production-grade, idareten çözüm yok** — her parça araştırmayla kanıtlanmış, teknik borç bırakmayan. (memory `nihai-amaca-uygunluk`)
- **Dil:** Kullanıcıya/araca dönen metin **Türkçe**; kod, tanımlayıcı, yorum, commit **İngilizce**. (CLAUDE.md)
- **Tek kullanıcı:** JARVIS yalnızca Kadir; çok-kullanıcı önceliklendirilmez ama depolama daima `user_id` ile anahtarlanır. (memory `kapsam-tek-kullanici`)
- **Politika tabanı korunur:** RED zone daima block; bilinmeyen araç = RED. Kimlik yalnızca **modüle eder**, tabanı gevşetmez. ([config.py:65-72](../../../brain/app/config.py#L65-L72))
- **Text yolu değişmez:** `policy_callback` trust sinyali yoksa `HIGH` varsayar → `/api/chat` davranışı birebir korunur; mevcut [test_policy.py](../../../brain/tests/test_policy.py) yeşil kalır.
- **DATA-log:** utterance başına `voice_score`, eşikler, `trust_level`, `presence`, `device_hint`, adapt-edildi-mi loglanır. (CLAUDE.md)
- **Geriye dönük uyum:** WS hello eski `{"token"}` formatını kabul eder (`device_hint="unknown"`, `presence="foreground"` default). Mevcut [test_voice.py](../../../brain/tests/test_voice.py) yeşil kalır.
- **Model kuralı:** ses-kimliği modeli `speechbrain/spkrec-ecapa-voxceleb` (Apache-2.0, 16kHz mono — bizim [voice_protocol.py:10](../../../brain/app/voice_protocol.py#L10) rate ile birebir). Gemini live modeli ilgisiz (mevcut resolver korunur).

---

## Dosya yapısı

| Dosya | Sorumluluk | Durum |
|---|---|---|
| `brain/app/trust.py` | `TrustContext`, `TrustLevel`, `assess()` — saf füzyon | YENİ |
| `brain/app/speaker.py` | `SpeakerProfile` (galeri math, saf), `embed()` (torch), `SpeakerService` (identify+adapt) | YENİ |
| `brain/app/speaker_store.py` | Firestore `speaker_profiles/{user_id}` tek-doküman load/save | YENİ |
| `brain/app/policy.py` | `policy_callback` → zone × trust modülasyonu | DEĞİŞİR |
| `brain/app/config.py` | eşikler, `TrustLevel` sabitleri, modülasyon | DEĞİŞİR |
| `brain/app/voice_protocol.py` | hello genişler (`device_hint`,`presence`), `evt_speaker` | DEĞİŞİR |
| `brain/app/voice.py` | `VoiceBridge` — utterance buffer, verify, trust, evt_speaker | DEĞİŞİR |
| `brain/app/main.py` | `POST /api/voice/enroll`; speaker_service wiring | DEĞİŞİR |
| `brain/pyproject.toml` | `[speaker]` opsiyonel extra (torch/torchaudio/speechbrain) | DEĞİŞİR |
| `brain/tests/test_trust.py`, `test_speaker.py`, `test_speaker_store.py`, `test_speaker_service.py`, `test_enroll.py`, `test_speaker_e2e.py` | test | YENİ |
| `brain/tests/fixtures/*.pcm` | ses fixture'ları | YENİ |
| `brain/scripts/enroll_kadir.py` | WAV → /api/voice/enroll yardımcı script | YENİ |

---

## Task 1: Bağımlılık + ortam + ağırlık gate'i

**Amaç:** torch/speechbrain'in hedef Python'da kurulduğunu, 192-dim embedding ürettiğini ve container ağırlığını **ölçerek** in-process kararını de-risk etmek. Bu bir kapıdır — sonraki her task bunun geçtiğini varsayar.

**Files:**
- Modify: `brain/pyproject.toml`
- Create: `brain/scripts/speaker_smoke.py`
- Create: `brain/tests/fixtures/README.md`

**Interfaces:**
- Produces: `speechbrain.inference` import yolu + `encode_batch` embedding boyutu (beklenen **192**) — sonraki task'lar bunu kullanır.

- [ ] **Step 1: `[speaker]` opsiyonel extra ekle**

`brain/pyproject.toml` içinde `[project.optional-dependencies]` altına ekle:

```toml
speaker = ["speechbrain>=1.0", "torch>=2.2", "torchaudio>=2.2"]
```

- [ ] **Step 2: Hedef Python'da kur ve import yolunu doğrula**

Prod Python 3.12 ([Dockerfile](../../../brain/Dockerfile)). Yerel 3.14 venv torch wheel'i vermeyebilir. Torch-capable bir 3.12 venv kur ve içine `[speaker]` extra'sını yükle:

```bash
cd brain && python3.12 -m venv .venv-speaker && .venv-speaker/bin/pip install -e ".[speaker]"
```

Kurulum başarısızsa (3.12'de bile) plan durur — spec §3 sidecar kaçış kapısı devreye girer (bu task'ın çıktısı: "in-process fizibil mi?" kararı).

- [ ] **Step 3: Smoke script yaz — embedding boyutu + ağırlık**

`brain/scripts/speaker_smoke.py`:

```python
"""Task-1 gate: confirm speechbrain ECAPA loads, embeds, and report dims.
Run with the torch-capable interpreter (.venv-speaker)."""
import struct, math, sys

def _sine_pcm16(seconds=2.0, freq=220.0, rate=16000):
    n = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )

def main():
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    model = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir="/tmp/spkrec-ecapa",
        run_opts={"device": "cpu"},
    )
    pcm = _sine_pcm16()
    wav = (torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0).unsqueeze(0)
    emb = model.encode_batch(wav)
    print("EMBED_SHAPE", tuple(emb.shape))
    print("EMBED_DIM", emb.squeeze().numel())

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Smoke'u çalıştır, boyutu ve ağırlığı kaydet**

```bash
cd brain && .venv-speaker/bin/python scripts/speaker_smoke.py
du -sh .venv-speaker/lib/python3.12/site-packages/torch /tmp/spkrec-ecapa
```

Expected: `EMBED_DIM 192` (ECAPA standart). Torch ~ birkaç yüz MB — bu ölçüm spec §3 in-process/sidecar kararını doğrular. `EMBED_DIM` 192 değilse sonraki task'lardaki `SPEAKER_EMB_DIM` sabiti buna göre güncellenir.

- [ ] **Step 5: Fixture README + import yolunu belgele**

`brain/tests/fixtures/README.md` içine: ölçülen embedding boyutu, doğrulanan import (`from speechbrain.inference.speaker import EncoderClassifier`), torch/speechbrain kurulan sürümler, container ağırlığı ölçümü, in-process kararı.

- [ ] **Step 6: Commit**

```bash
git add brain/pyproject.toml brain/scripts/speaker_smoke.py brain/tests/fixtures/README.md
git commit -m "build(speaker): add [speaker] extra; gate ECAPA install + embed dim + weight"
```

---

## Task 2: `trust.py` — TrustContext + assess (saf)

**Files:**
- Create: `brain/app/trust.py`
- Test: `brain/tests/test_trust.py`

**Interfaces:**
- Produces: `TrustContext(auth_verified: bool, presence: str="foreground", voice_score: float|None=None, device_hint: str="unknown")`; sabitler `HIGH="HIGH"`, `MEDIUM="MEDIUM"`, `LOW="LOW"`; `assess(ctx: TrustContext, accept_threshold: float) -> str`.

- [ ] **Step 1: Failing test yaz**

`brain/tests/test_trust.py`:

```python
from app.trust import TrustContext, assess, HIGH, MEDIUM, LOW

TH = 0.5  # accept threshold for tests

def test_no_auth_is_low():
    assert assess(TrustContext(auth_verified=False), TH) == LOW

def test_foreground_is_high_regardless_of_voice():
    assert assess(TrustContext(auth_verified=True, presence="foreground", voice_score=0.0), TH) == HIGH
    assert assess(TrustContext(auth_verified=True, presence="foreground", voice_score=None), TH) == HIGH

def test_text_path_no_voice_defaults_high_when_foreground():
    # voice_score None (text chat) + default foreground presence -> HIGH
    assert assess(TrustContext(auth_verified=True), TH) == HIGH

def test_locked_no_voice_is_medium():
    assert assess(TrustContext(auth_verified=True, presence="locked", voice_score=None), TH) == MEDIUM

def test_locked_voice_match_is_medium():
    assert assess(TrustContext(auth_verified=True, presence="locked", voice_score=0.9), TH) == MEDIUM

def test_locked_voice_mismatch_is_low():
    assert assess(TrustContext(auth_verified=True, presence="ambient", voice_score=0.1), TH) == LOW
```

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_trust.py -v`
Expected: FAIL (`ModuleNotFoundError: app.trust`)

- [ ] **Step 3: `trust.py` yaz**

```python
"""Risk-based identity trust fusion (Katman 2b Dilim 3a, spec §6).

Fuses whatever identity/context signals are available into a coarse TrustLevel
that the policy layer (policy.py) consumes to modulate the action zone matrix.
Extensible: new signals become new fields + fusion lines; the enum stays the
same. Missing signals degrade gracefully."""
from dataclasses import dataclass

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"


@dataclass
class TrustContext:
    auth_verified: bool
    presence: str = "foreground"       # foreground | locked | ambient | ...
    voice_score: float | None = None   # None = no audio evidence (e.g. text chat)
    device_hint: str = "unknown"       # phone | headset | tablet | unknown


def assess(ctx: TrustContext, accept_threshold: float) -> str:
    """foreground (unlocked) is always HIGH -- daily-life leniency, voice only
    annotates/feeds. locked/ambient is structurally capped at MEDIUM and drops
    to LOW when a present voice fails to match. See spec §6 table; the exact
    cells are tunable via config, not sacred."""
    if not ctx.auth_verified:
        return LOW
    if ctx.presence == "foreground":
        return HIGH
    if ctx.voice_score is None:
        return MEDIUM
    return MEDIUM if ctx.voice_score >= accept_threshold else LOW
```

- [ ] **Step 4: Testin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_trust.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add brain/app/trust.py brain/tests/test_trust.py
git commit -m "feat(speaker): trust fusion — TrustContext + assess (foreground-lenient, locked-strict)"
```

---

## Task 3: `speaker.py` — SpeakerProfile galeri math (saf, torch'suz)

**Files:**
- Create: `brain/app/speaker.py`
- Test: `brain/tests/test_speaker.py`

**Interfaces:**
- Produces: `SpeakerProfile(anchors: list[list[float]], adaptive: list[dict])` where each adaptive item is `{"vec": list[float], "device_hint": str, "ts": str}`; `.score(vec, top_k) -> float`; `.adapt(vec, device_hint, cap, now_fn) -> None` (append + evict, anchors immutable); `.all_vectors() -> list[list[float]]`.

- [ ] **Step 1: Failing test yaz**

`brain/tests/test_speaker.py`:

```python
from app.speaker import SpeakerProfile

A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]

def _clock():
    seq = iter(["t0", "t1", "t2", "t3", "t4", "t5", "t6"])
    return lambda: next(seq)

def test_score_top_k_cosine_to_gallery():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    assert p.score(A, top_k=1) == 1.0            # identical -> 1.0
    assert p.score(B, top_k=1) == 0.0            # orthogonal -> 0.0

def test_score_uses_both_anchor_and_adaptive():
    p = SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "phone", "ts": "t"}])
    # query close to B should score high via the adaptive sample
    assert p.score([0.0, 0.99, 0.0], top_k=1) > 0.99

def test_adapt_appends_adaptive_sample():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    p.adapt(B, "headset", cap=5, now_fn=lambda: "t1")
    assert len(p.adaptive) == 1
    assert p.adaptive[0] == {"vec": B, "device_hint": "headset", "ts": "t1"}

def test_adapt_evicts_oldest_over_cap_but_keeps_anchors():
    p = SpeakerProfile(anchors=[A], adaptive=[])
    clk = _clock()
    for _ in range(4):
        p.adapt(B, "phone", cap=2, now_fn=clk)
    assert len(p.adaptive) == 2                  # capped
    assert p.anchors == [A]                       # anchors never touched
    assert p.adaptive[-1]["ts"] == "t3"          # newest kept
    assert p.adaptive[0]["ts"] == "t2"           # oldest two evicted
```

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker.py -v`
Expected: FAIL (`ModuleNotFoundError: app.speaker`)

- [ ] **Step 3: `SpeakerProfile` yaz** (embed/service AŞAĞIDAKİ task'larda; bu dosya torch'suz başlar)

```python
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
            self.adaptive = self.adaptive[-cap:]
```

- [ ] **Step 4: Testin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add brain/app/speaker.py brain/tests/test_speaker.py
git commit -m "feat(speaker): SpeakerProfile adaptive gallery — top-k cosine + capped evict, anchors immutable"
```

---

## Task 4: `speaker.py` embed() — ECAPA PCM→vektör (torch)

**Amaç:** ham PCM16 16kHz → 192-dim embedding. Torch-bağımlı → torch-capable interpreter (Task 1 `.venv-speaker`) ile test edilir; determinizm + konuşmacı-ayrımı gerçek ses fixture'larıyla kanıtlanır.

**Files:**
- Modify: `brain/app/speaker.py`
- Create: `brain/tests/fixtures/spk_a_1.pcm`, `spk_a_2.pcm`, `spk_b_1.pcm` (PCM16 mono 16kHz, ~2-3sn)
- Test: `brain/tests/test_speaker_embed.py`

**Interfaces:**
- Produces: `pcm16_to_tensor(pcm: bytes)`; `embed(pcm: bytes) -> list[float]` (192 float).

- [ ] **Step 1: Fixture ses klipleri üret**

İki farklı konuşmacıdan ~2-3sn'lik klipler gerekir (aynı konuşmacı→yüksek, farklı→düşük cosine kanıtı için). Public-domain LibriSpeech (CC-BY) klipleri veya iki farklı kişinin kısa kaydı → 16kHz mono PCM16'ya dönüştür:

```bash
# örnek: ffmpeg ile herhangi bir konuşma klibinden 16k mono PCM16 raw
ffmpeg -i speakerA_utt1.wav -ac 1 -ar 16000 -f s16le brain/tests/fixtures/spk_a_1.pcm
ffmpeg -i speakerA_utt2.wav -ac 1 -ar 16000 -f s16le brain/tests/fixtures/spk_a_2.pcm
ffmpeg -i speakerB_utt1.wav -ac 1 -ar 16000 -f s16le brain/tests/fixtures/spk_b_1.pcm
```

`spk_a_*` aynı konuşmacının iki farklı utterance'ı; `spk_b_1` farklı konuşmacı. Kaynağı `fixtures/README.md`'ye yaz (lisans dahil).

- [ ] **Step 2: Failing test yaz**

`brain/tests/test_speaker_embed.py`:

```python
import pathlib
import pytest

pytest.importorskip("torch")            # torch yoksa (3.14 venv) atla
pytest.importorskip("speechbrain")

from app.speaker import embed
from app.memory import _cosine_similarity

FIX = pathlib.Path(__file__).parent / "fixtures"

def _pcm(name):
    return (FIX / name).read_bytes()

def test_embed_dim_is_192():
    assert len(embed(_pcm("spk_a_1.pcm"))) == 192

def test_embed_is_deterministic():
    assert embed(_pcm("spk_a_1.pcm")) == embed(_pcm("spk_a_1.pcm"))

def test_same_speaker_scores_higher_than_different():
    a1 = embed(_pcm("spk_a_1.pcm"))
    a2 = embed(_pcm("spk_a_2.pcm"))
    b1 = embed(_pcm("spk_b_1.pcm"))
    assert _cosine_similarity(a1, a2) > _cosine_similarity(a1, b1)
```

- [ ] **Step 3: Testin fail ettiğini doğrula** (torch-capable interpreter ile)

Run: `cd brain && .venv-speaker/bin/python -m pytest tests/test_speaker_embed.py -v`
Expected: FAIL (`ImportError: cannot import name 'embed'`)

- [ ] **Step 4: `embed()` ekle** (`brain/app/speaker.py` sonuna)

```python
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
```

- [ ] **Step 5: Testin geçtiğini doğrula**

Run: `cd brain && .venv-speaker/bin/python -m pytest tests/test_speaker_embed.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add brain/app/speaker.py brain/tests/test_speaker_embed.py brain/tests/fixtures/*.pcm brain/tests/fixtures/README.md
git commit -m "feat(speaker): ECAPA embed() PCM16->192-dim + determinism/separation tests"
```

---

## Task 5: `speaker_store.py` — Firestore tek-doküman voiceprint

**Amaç:** Voiceprint galerisini `speaker_profiles/{user_id}` **tek dokümanında** sakla (FakeDB alt-koleksiyon modellemiyor + galeri sınırlı ~43KB ≪ 1MB). Embedding'ler düz float listesi (in-process cosine, find_nearest gerekmez).

**Files:**
- Create: `brain/app/speaker_store.py`
- Test: `brain/tests/test_speaker_store.py`

**Interfaces:**
- Consumes: `SpeakerProfile` (Task 3).
- Produces: `load_profile(db, user_id) -> SpeakerProfile`; `save_profile(db, user_id, profile) -> None`; `enroll_anchors(db, user_id, vecs) -> None`.

- [ ] **Step 1: Failing test yaz**

`brain/tests/test_speaker_store.py`:

```python
from app.speaker import SpeakerProfile
from app.speaker_store import load_profile, save_profile, enroll_anchors
from tests.fakes import FakeDB

A = [1.0, 0.0]
B = [0.0, 1.0]

def test_load_missing_returns_empty_profile():
    p = load_profile(FakeDB(), "kadir@example.com")
    assert p.anchors == [] and p.adaptive == []

def test_save_then_load_round_trips():
    db = FakeDB()
    save_profile(db, "kadir@example.com",
                 SpeakerProfile(anchors=[A], adaptive=[{"vec": B, "device_hint": "phone", "ts": "t"}]))
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A]
    assert p.adaptive == [{"vec": B, "device_hint": "phone", "ts": "t"}]

def test_enroll_anchors_appends():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A, B])
    p = load_profile(db, "kadir@example.com")
    assert p.anchors == [A, B]

def test_profiles_are_user_keyed():
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [A])
    assert load_profile(db, "someone@else.com").anchors == []
```

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker_store.py -v`
Expected: FAIL (`ModuleNotFoundError: app.speaker_store`)

- [ ] **Step 3: `speaker_store.py` yaz**

```python
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
```

- [ ] **Step 4: Testin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add brain/app/speaker_store.py brain/tests/test_speaker_store.py
git commit -m "feat(speaker): Firestore single-doc voiceprint store (user-keyed, plain-list embeddings)"
```

---

## Task 6: `SpeakerService` — identify + koşullu adapt orkestrasyonu

**Amaç:** embed + profil skorlama + eşik kararı + koşullu adaptasyon + kalıcılaştırmayı tek yerde birleştir. `embed`'i enjekte edilebilir yap → torch'suz test (fake embed lambda).

**Files:**
- Modify: `brain/app/speaker.py`
- Test: `brain/tests/test_speaker_service.py`

**Interfaces:**
- Consumes: `SpeakerProfile`, `speaker_store`, `config` eşikleri.
- Produces: `SpeakerService(db, embed_fn=embed, now_fn=..., accept=..., adapt=..., cap=..., top_k=...)`; `.identify(user_id, pcm, device_hint, auth_is_kadir) -> tuple[bool, float]` (verified, score). ADAPT eşiği + auth geçildiğinde profili besler ve kalıcılaştırır.

- [ ] **Step 1: Failing test yaz**

`brain/tests/test_speaker_service.py`:

```python
from app.speaker import SpeakerService
from app.speaker_store import enroll_anchors, load_profile
from tests.fakes import FakeDB

A = [1.0, 0.0, 0.0]
NEAR_A = [0.98, 0.02, 0.0]
FAR = [0.0, 1.0, 0.0]

def _svc(db, **kw):
    # embed_fn maps a 1-byte tag to a canned vector, so no torch needed
    vecs = {b"A": A, b"N": NEAR_A, b"F": FAR}
    return SpeakerService(db, embed_fn=lambda pcm: vecs[pcm], now_fn=lambda: "t",
                          accept=0.9, adapt=0.97, cap=5, top_k=1, **kw)

def test_matching_voice_verified():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    verified, score = _svc(db).identify("k", b"A", "phone", auth_is_kadir=True)
    assert verified is True and score == 1.0

def test_different_voice_not_verified():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    verified, score = _svc(db).identify("k", b"F", "phone", auth_is_kadir=True)
    assert verified is False

def test_high_confidence_adapts_and_persists():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    _svc(db).identify("k", b"A", "headset", auth_is_kadir=True)     # score 1.0 >= adapt
    assert len(load_profile(db, "k").adaptive) == 1
    assert load_profile(db, "k").adaptive[0]["device_hint"] == "headset"

def test_accepted_but_below_adapt_does_not_feed():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    verified, score = _svc(db).identify("k", b"N", "phone", auth_is_kadir=True)  # 0.9<=score<0.97
    assert verified is True
    assert load_profile(db, "k").adaptive == []                    # not fed (poisoning guard band)

def test_no_adapt_when_not_authed_kadir():
    db = FakeDB(); enroll_anchors(db, "k", [A])
    _svc(db).identify("k", b"A", "phone", auth_is_kadir=False)      # high score but not authed
    assert load_profile(db, "k").adaptive == []
```

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker_service.py -v`
Expected: FAIL (`ImportError: cannot import name 'SpeakerService'`)

- [ ] **Step 3: `SpeakerService` ekle** (`brain/app/speaker.py` sonuna)

```python
from datetime import datetime, timezone

from . import speaker_store


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SpeakerService:
    """Orchestrates one utterance's identity check: embed -> score against the
    user's gallery -> verified?(>=accept) -> conditionally self-feed (>=adapt AND
    authed as Kadir) -> persist. embed_fn is injectable so the logic is testable
    without torch. Two thresholds create a poisoning-guard band: accept <= score
    < adapt means "trust it but don't learn from it" (spec §5)."""

    def __init__(self, db, embed_fn=embed, now_fn=_utc_now,
                 accept: float = 0.35, adapt: float = 0.6, cap: int = 20, top_k: int = 3):
        self.db = db
        self.embed_fn = embed_fn
        self.now_fn = now_fn
        self.accept = accept
        self.adapt = adapt
        self.cap = cap
        self.top_k = top_k

    def identify(self, user_id: str, pcm: bytes, device_hint: str,
                 auth_is_kadir: bool) -> tuple[bool, float]:
        vec = self.embed_fn(pcm)
        profile = speaker_store.load_profile(self.db, user_id)
        score = profile.score(vec, self.top_k)
        verified = score >= self.accept
        adapted = score >= self.adapt and auth_is_kadir
        if adapted:
            profile.adapt(vec, device_hint, self.cap, self.now_fn)
            speaker_store.save_profile(self.db, user_id, profile)
        import logging
        logging.info(
            "speaker.identify: user=%s score=%.4f verified=%s adapted=%s "
            "device=%s anchors=%d adaptive=%d accept=%.2f adapt=%.2f",
            user_id, score, verified, adapted, device_hint,
            len(profile.anchors), len(profile.adaptive), self.accept, self.adapt,
        )
        return verified, score
```

Not: `accept`/`adapt` default değerleri (0.35/0.6) cosine benzerliği için başlangıç tahminidir; Task 1 fixture'ları + gerçek enrollment sonrası kalibre edilir (spec §15). `config.py`'den okunacak sabitler Task 7'de eklenir.

- [ ] **Step 4: Testin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker_service.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add brain/app/speaker.py brain/tests/test_speaker_service.py
git commit -m "feat(speaker): SpeakerService identify + guarded self-feed (accept<adapt band, auth-gated)"
```

---

## Task 7: Politika modülasyonu — `policy.py` + `config.py`

**Amaç:** `policy_callback` `tool_context.state`'ten `trust_level` okuyup zone × trust matrisiyle karar versin. Trust yoksa/`tool_context` None ise **HIGH** → text yolu + mevcut testler değişmez.

**Files:**
- Modify: `brain/app/config.py`, `brain/app/policy.py`
- Test: `brain/tests/test_policy.py` (ekle), mevcut testler korunur

**Interfaces:**
- Consumes: `trust.HIGH/MEDIUM/LOW`.
- Produces: config sabitleri `SPEAKER_ACCEPT_THRESHOLD`, `SPEAKER_ADAPT_THRESHOLD`, `SPEAKER_TOPK`, `SPEAKER_ADAPTIVE_CAP`, `TRUST_STATE_KEY="trust_level"`; `policy_callback` artık `tool_context.state`'ten trust okur.

- [ ] **Step 1: config sabitleri ekle** (`brain/app/config.py` sonuna)

```python
# Speaker identity (Katman 2b Dilim 3a) — cosine thresholds are starting
# estimates, calibrated after enrollment (spec §15).
SPEAKER_ACCEPT_THRESHOLD = float(os.environ.get("JARVIS_SPEAKER_ACCEPT", "0.35"))
SPEAKER_ADAPT_THRESHOLD = float(os.environ.get("JARVIS_SPEAKER_ADAPT", "0.60"))
SPEAKER_TOPK = int(os.environ.get("JARVIS_SPEAKER_TOPK", "3"))
SPEAKER_ADAPTIVE_CAP = int(os.environ.get("JARVIS_SPEAKER_ADAPTIVE_CAP", "20"))
TRUST_STATE_KEY = "trust_level"   # session.state key the voice bridge writes
```

- [ ] **Step 2: Failing test yaz** (`brain/tests/test_policy.py`'ye ekle)

```python
from types import SimpleNamespace
from app import trust

def _ctx(level):
    # mirrors ADK ToolContext: .state is a dict-like mapping backed by session.state
    return SimpleNamespace(state={config.TRUST_STATE_KEY: level})

def test_yellow_tool_blocked_when_trust_medium():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.MEDIUM))
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["decision"] == "block"
    assert audit.entries[0]["trust"] == trust.MEDIUM

def test_yellow_tool_allowed_when_trust_high():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.HIGH))
    assert result is None
    assert audit.entries[0]["decision"] == "allow"

def test_missing_tool_context_defaults_high():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    # green tool, tool_context None (text path / existing tests) -> allow, HIGH
    result = cb(_tool("get_user_profile"), {}, None)
    assert result is None
    assert audit.entries[0]["trust"] == trust.HIGH

def test_yellow_tool_blocked_when_trust_low():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.LOW))
    assert result is not None
    assert audit.entries[0]["decision"] == "block"
```

- [ ] **Step 3: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_policy.py -v`
Expected: yeni testler FAIL (KeyError `"trust"` / yanlış decision), eski testler PASS

- [ ] **Step 4: `policy.py` güncelle**

`brain/app/policy.py` — importlara `from . import trust` ekle; `policy_callback`'i değiştir:

```python
def _read_trust(tool_context) -> str:
    """Read the trust level the voice bridge wrote into session.state; default
    HIGH when absent (text path, or first utterance) so nothing is restricted
    unless a low-trust voice context explicitly lowered it. tool_context.state
    is ADK's State (backed by session.state); tolerate None/missing."""
    state = getattr(tool_context, "state", None)
    if not state:
        return trust.HIGH
    try:
        return state.get(config.TRUST_STATE_KEY, trust.HIGH)
    except Exception:
        return trust.HIGH


def _decide(zone: str, trust_level: str) -> str:
    """zone × trust matrix (spec §7). RED always blocks. HIGH keeps today's
    behavior. MEDIUM/LOW escalate YELLOW -> confirm (reuse of the block-with-
    message pattern). GREEN stays allowed."""
    if zone == config.ZONE_RED:
        return "block"
    if trust_level == trust.HIGH:
        return "dry_run" if config.DRY_RUN else "allow"
    if zone == config.ZONE_YELLOW:      # MEDIUM or LOW
        return "confirm"
    return "dry_run" if config.DRY_RUN else "allow"


def make_policy_callback(audit: AuditWriter):
    def policy_callback(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
        zone = check_zone(tool.name)
        trust_level = _read_trust(tool_context)
        decision = _decide(zone, trust_level)
        audit.write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "orchestrator",
            "tool": tool.name,
            "args": {k: str(v)[:500] for k, v in (args or {}).items()},
            "zone": zone,
            "trust": trust_level,
            "decision": decision,
        })
        if decision == "block":
            return {"result": (
                f"POLİTİKA ENGELİ: '{tool.name}' kırmızı bölgede — onaysız çalıştırılamaz. "
                "Kadir'e ne yapmak istediğini söyle ve onay iste."
            )}
        if decision == "confirm":
            return {"result": (
                f"GÜVEN DÜŞÜK: '{tool.name}' şu an düşük-güven bağlamında (kimlik doğrulanmadı). "
                "Çalıştırmadan önce Kadir'den açık onay iste."
            )}
        if decision == "dry_run":
            return {"result": f"DRY-RUN: '{tool.name}' şu argümanlarla çalışacaktı: {args}"}
        return None

    return policy_callback
```

- [ ] **Step 5: Tüm politika testlerinin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_policy.py -v`
Expected: PASS (eski + yeni hepsi)

- [ ] **Step 6: Commit**

```bash
git add brain/app/config.py brain/app/policy.py brain/tests/test_policy.py
git commit -m "feat(speaker): identity-aware policy — zone×trust modulation, default HIGH keeps text path intact"
```

---

## Task 8: WS protokol genişletme — `voice_protocol.py`

**Amaç:** hello frame `device_hint`/`presence` taşısın (geriye dönük uyumlu); `evt_speaker` event'i eklensin. `parse_hello` artık dict döndürür → `voice.py._handshake` uyarlanır (Task 9).

**Files:**
- Modify: `brain/app/voice_protocol.py`
- Test: `brain/tests/test_voice_protocol.py` (ekle)

**Interfaces:**
- Produces: `parse_hello(raw) -> dict` with keys `token: str`, `device_hint: str` (default `"unknown"`), `presence: str` (default `"foreground"`); `evt_speaker(role, verified, score) -> dict`.

- [ ] **Step 1: Failing test yaz** (`brain/tests/test_voice_protocol.py`'ye ekle)

```python
import pytest
from app import voice_protocol as vp

def test_parse_hello_full():
    h = vp.parse_hello('{"token":"t","device_hint":"headset","presence":"locked"}')
    assert h == {"token": "t", "device_hint": "headset", "presence": "locked"}

def test_parse_hello_backward_compatible_defaults():
    h = vp.parse_hello('{"token":"t"}')
    assert h == {"token": "t", "device_hint": "unknown", "presence": "foreground"}

def test_parse_hello_missing_token_raises():
    with pytest.raises(ValueError):
        vp.parse_hello('{"device_hint":"phone"}')

def test_evt_speaker_shape():
    assert vp.evt_speaker("user", True, 0.87) == {
        "type": "speaker", "role": "user", "verified": True, "score": 0.87,
    }
```

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_voice_protocol.py -v`
Expected: yeni testler FAIL (`parse_hello` str döndürüyor / `evt_speaker` yok)

- [ ] **Step 3: `voice_protocol.py` güncelle**

`evt_speaker` ekle ve `parse_hello`'yu değiştir:

```python
def evt_speaker(role: str, verified: bool, score: float) -> dict:
    return {"type": "speaker", "role": role, "verified": verified, "score": score}


def parse_hello(raw: str) -> dict:
    """First TEXT frame: {"token", "device_hint"?, "presence"?}. device_hint and
    presence are optional (backward compatible with the token-only Katman 2a/2b
    hello); they feed the risk-based trust fusion (spec §6, §11)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("hello frame is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("hello frame is not a JSON object")
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("hello frame missing token")
    return {
        "token": token,
        "device_hint": data.get("device_hint") or "unknown",
        "presence": data.get("presence") or "foreground",
    }
```

- [ ] **Step 4: Testin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_voice_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add brain/app/voice_protocol.py brain/tests/test_voice_protocol.py
git commit -m "feat(speaker): extend WS hello (device_hint/presence, backward-compatible) + evt_speaker"
```

---

## Task 9: VoiceBridge entegrasyonu — `voice.py`

**Amaç:** Bridge utterance PCM'ini biriktirsin; `input_transcription` sınırında `SpeakerService.identify` çağırsın; `session.state`'e trust yazsın; `evt_speaker` yollasın. `speaker_service=None` iken no-op → mevcut [test_voice.py](../../../brain/tests/test_voice.py) yeşil kalır.

**Files:**
- Modify: `brain/app/voice.py`
- Test: `brain/tests/test_voice.py` (ekle)

**Interfaces:**
- Consumes: `SpeakerService.identify` (Task 6), `trust.assess` (Task 2), `vp.evt_speaker` (Task 8), `config` sabitleri.
- Produces: `VoiceBridge(runner, session_service, memory=None, speaker_service=None, device_hint="unknown", presence="foreground", session=None)`.

- [ ] **Step 1: Failing test yaz** (`brain/tests/test_voice.py`'ye ekle)

```python
from app import config, trust

class FakeSpeaker:
    def __init__(self, result):  # (verified, score)
        self.result = result
        self.calls = []
    def identify(self, user_id, pcm, device_hint, auth_is_kadir):
        self.calls.append((user_id, pcm, device_hint, auth_is_kadir))
        return self.result

@pytest.mark.asyncio
async def test_bridge_verifies_utterance_and_writes_trust_and_event():
    session = type("S", (), {"state": {}})()
    speaker = FakeSpeaker((True, 0.9))

    async def fake_events():
        yield _make_event(data=b"\x00\x01")                       # mic-ish (ignored here)
        yield _make_event(input_transcription=FakeTranscription("merhaba"))

    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None, speaker_service=speaker,
                         device_hint="headset", presence="locked", session=session)
    bridge._utterance = bytearray(b"\x00\x01\x02\x03")            # buffered mic audio
    await bridge._pump_events(fake_events(), ws)

    assert speaker.calls and speaker.calls[0][2] == "headset"     # identify called w/ device
    # locked + verified -> MEDIUM in session.state
    assert session.state[config.TRUST_STATE_KEY] == trust.MEDIUM
    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": True, "score": 0.9})) in ws.sent

@pytest.mark.asyncio
async def test_bridge_speaker_none_is_noop():
    async def fake_events():
        yield _make_event(input_transcription=FakeTranscription("selam"))
    ws = FakeWS([])
    bridge = VoiceBridge(runner=None, session_service=None)       # no speaker_service
    await bridge._pump_events(fake_events(), ws)                  # must not raise
    # only the transcript event, no speaker event
    assert all("speaker" not in t for _, t in ws.sent if isinstance(t, str))
```

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_voice.py -v`
Expected: yeni testler FAIL (yeni ctor argümanları/davranış yok), eski testler PASS

- [ ] **Step 3: `voice.py` güncelle**

`VoiceBridge.__init__` genişlet ve mic buffering + verify ekle. `_pump_mic_once`'a buffer append; `_pump_events`'te input_transcription'da verify.

```python
from . import config, trust
```

`__init__`:

```python
    def __init__(self, runner, session_service, memory=None, speaker_service=None,
                 device_hint="unknown", presence="foreground", session=None):
        self.runner = runner
        self.session_service = session_service
        self.memory = memory
        self.speaker_service = speaker_service
        self.device_hint = device_hint
        self.presence = presence
        self.session = session          # ADK session; state written here for policy
        self.transcript: list[dict] = []
        self._utterance = bytearray()   # accumulates this turn's mic PCM
        self._user_id = ""
```

`_pump_mic_once` içinde `data` iletilirken buffer'a da ekle:

```python
        if data := msg.get("bytes"):
            if self.speaker_service is not None:
                self._utterance.extend(data)
            queue.send_realtime(types.Blob(data=data, mime_type=vp.AUDIO_MIME_IN))
```

`_pump_events` içinde input_transcription bulunduğunda (user rolü) verify çağır. Mevcut transcript döngüsünün içine, `role == "user"` dalına, verify ekle:

```python
            for tr_attr, role in (("input_transcription", "user"), ("output_transcription", "jarvis")):
                tr = getattr(event, tr_attr, None)
                if tr and getattr(tr, "text", None):
                    await ws.send_text(json.dumps(vp.evt_transcript(role, tr.text)))
                    self.transcript.append({"role": role, "text": tr.text})
                    if role == "user" and self.speaker_service is not None:
                        await self._verify_utterance(ws)
```

Yeni metod:

```python
    async def _verify_utterance(self, ws) -> None:
        """Called at the user utterance boundary: run speaker identity on the
        buffered PCM, fuse into a trust level, write it into session.state for
        the policy layer, and tell the client. Any failure is logged and treated
        as unverified -- it must never break the audio stream."""
        pcm = bytes(self._utterance)
        self._utterance.clear()
        if not pcm:
            return
        try:
            verified, score = self.speaker_service.identify(
                self._user_id, pcm, self.device_hint, auth_is_kadir=True
            )
        except Exception:
            logging.exception("voice bridge: speaker.identify failed for %s", self._user_id)
            verified, score = False, 0.0
        level = trust.assess(
            trust.TrustContext(
                auth_verified=True, presence=self.presence,
                voice_score=score if verified else 0.0, device_hint=self.device_hint,
            ),
            config.SPEAKER_ACCEPT_THRESHOLD,
        )
        if self.session is not None:
            self.session.state[config.TRUST_STATE_KEY] = level
        await ws.send_text(json.dumps(vp.evt_speaker("user", verified, score)))
        logging.info(
            "voice trust: user=%s verified=%s score=%.4f presence=%s device=%s level=%s",
            self._user_id, verified, score, self.presence, self.device_hint, level,
        )
```

`run()` içinde `self._user_id = user_id` ata (session oluşturulduktan sonra, `self.session`'ı da sakla):

```python
        session = await self.session_service.get_session(
            app_name="jarvis", user_id=user_id, session_id=session_id
        )
        if session is None:
            session = await self.session_service.create_session(
                app_name="jarvis", user_id=user_id, session_id=session_id
            )
        self.session = session
        self._user_id = user_id
```

- [ ] **Step 4: Testin geçtiğini doğrula (yeni + mevcut)**

Run: `cd brain && .venv/bin/python -m pytest tests/test_voice.py -v`
Expected: PASS (yeni 2 + mevcut hepsi)

- [ ] **Step 5: Commit**

```bash
git add brain/app/voice.py brain/tests/test_voice.py
git commit -m "feat(speaker): VoiceBridge per-utterance identity -> trust in session.state + evt_speaker"
```

---

## Task 10: Enroll endpoint + wiring — `main.py`

**Amaç:** `POST /api/voice/enroll` (require_user) kayıtlı ses kliplerini anchor olarak saklasın; `ws_voice` handshake `device_hint`/`presence` çıkarıp bridge'e ve speaker_service'e bağlasın.

**Files:**
- Modify: `brain/app/main.py`, `brain/app/voice.py` (`_handshake` + `ws_voice`)
- Test: `brain/tests/test_enroll.py`

**Interfaces:**
- Consumes: `speaker.embed`, `speaker_store.enroll_anchors`, `require_user`.
- Produces: `POST /api/voice/enroll` body `{"clips": ["<base64 pcm16 16k>", ...]}` → `{"anchors": <toplam>}`.

- [ ] **Step 1: Failing test yaz**

`brain/tests/test_enroll.py`:

```python
import base64
import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeDB

@pytest.fixture
def client(monkeypatch):
    import app.main as main
    db = FakeDB()
    monkeypatch.setattr(main, "_init", lambda: None)
    monkeypatch.setattr(main, "_enroll_db", lambda: db, raising=False)
    monkeypatch.setattr("app.main.require_user", lambda: "kadir@example.com")
    # patch embed so no torch: map any pcm to a fixed vector
    monkeypatch.setattr("app.speaker.embed", lambda pcm: [1.0, 0.0])
    main.app.dependency_overrides = {}
    return TestClient(main.app), db, main

def test_enroll_stores_anchors(client):
    c, db, main = client
    main.app.dependency_overrides[main.require_user] = lambda: "kadir@example.com"
    clip = base64.b64encode(b"\x00\x01\x00\x01").decode()
    r = c.post("/api/voice/enroll", json={"clips": [clip, clip]})
    assert r.status_code == 200
    from app.speaker_store import load_profile
    assert len(load_profile(db, "kadir@example.com").anchors) == 2
```

Not: enroll'ün DB'yi nasıl aldığı ([main.py](../../../brain/app/main.py) `_init` lazy deseni gibi) implementasyonda netleşir; test `_init`'i stub'lar. Gerçek DB erişimi mevcut `_memory.db`/firestore client üzerinden; enroll bir `_enroll_db()` yardımcısıyla firestore client alır (test'te patch'lenir).

- [ ] **Step 2: Testin fail ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_enroll.py -v`
Expected: FAIL (endpoint yok → 404)

- [ ] **Step 3: `main.py`'ye endpoint ekle**

```python
import base64
from . import speaker, speaker_store

def _enroll_db():
    _init()
    return _memory.db      # reuse the firestore client Memory already holds


class EnrollRequest(BaseModel):
    clips: list[str]       # base64-encoded PCM16 mono 16kHz utterances


@app.post("/api/voice/enroll")
async def enroll(req: EnrollRequest, email: str = Depends(require_user)):
    if not req.clips:
        raise HTTPException(status_code=400, detail="En az bir ses klibi gerekli")
    try:
        vecs = [speaker.embed(base64.b64decode(clip)) for clip in req.clips]
        speaker_store.enroll_anchors(_enroll_db(), email, vecs)
    except Exception:
        logging.exception("enroll failed for %s", email)
        raise HTTPException(status_code=502, detail="Ses kaydı işlenemedi, tekrar dene")
    total = len(speaker_store.load_profile(_enroll_db(), email).anchors)
    return {"anchors": total}
```

- [ ] **Step 4: `voice.py` `_handshake` + `ws_voice` wiring**

`_handshake` artık `(email, device_hint, presence)` döndürsün:

```python
async def _handshake(ws: WebSocket) -> tuple[str, str, str] | None:
    try:
        hello = await ws.receive_text()
    except WebSocketDisconnect:
        return None
    try:
        parsed = vp.parse_hello(hello)
        email = verify_token_email(parsed["token"])
        return email, parsed["device_hint"], parsed["presence"]
    except (ValueError, PermissionError):
        await ws.send_text(json.dumps(vp.evt_error("Giriş doğrulanamadı")))
        await ws.close(code=4401)
        return None
```

`ws_voice`:

```python
@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    await ws.accept()
    hs = await _handshake(ws)
    if hs is None:
        return
    email, device_hint, presence = hs
    from . import main

    try:
        runner, sessions, memory = main.get_voice_runner_sessions_memory()
        speaker_service = main.get_speaker_service()
        await VoiceBridge(
            runner, sessions, memory=memory, speaker_service=speaker_service,
            device_hint=device_hint, presence=presence,
        ).run(ws, user_id=email)
    except Exception:
        logging.exception("voice bridge failed for %s", email)
        await ws.send_text(json.dumps(vp.evt_error("Sesli oturum düştü, tekrar bağlan")))
        await ws.close(code=1011)
```

`main.py`'ye accessor:

```python
_speaker_service = None

def get_speaker_service():
    global _speaker_service
    if _speaker_service is None:
        _speaker_service = speaker.SpeakerService(
            _enroll_db(),
            accept=config.SPEAKER_ACCEPT_THRESHOLD,
            adapt=config.SPEAKER_ADAPT_THRESHOLD,
            top_k=config.SPEAKER_TOPK,
            cap=config.SPEAKER_ADAPTIVE_CAP,
        )
    return _speaker_service
```

Mevcut `test_voice.py` handshake testleri `_handshake` dönüşünü kontrol ediyor — onları yeni tuple dönüşüne uyarlaman gerekir (aynı commit'te). `test_handshake_valid_hello_returns_email`: `email, *_ = await _handshake(ws)` biçimine güncelle.

- [ ] **Step 5: Testlerin geçtiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_enroll.py tests/test_voice.py -v`
Expected: PASS (enroll + uyarlanmış handshake dahil)

- [ ] **Step 6: Commit**

```bash
git add brain/app/main.py brain/app/voice.py brain/tests/test_enroll.py brain/tests/test_voice.py
git commit -m "feat(speaker): POST /api/voice/enroll + wire speaker_service/device/presence into ws_voice"
```

---

## Task 11: WS e2e harness + enroll yardımcı script

**Amaç:** Gerçek `/ws/voice` üzerinden (FastAPI TestClient websocket + fake runner + gerçek/fake speaker) uçtan uca kanıt: hello(device/presence) → PCM → `evt_speaker` + locked-context sıkılaşması. Ayrıca WAV→enroll script'i.

**Files:**
- Create: `brain/tests/test_speaker_e2e.py`
- Create: `brain/scripts/enroll_kadir.py`

**Interfaces:**
- Consumes: `app.main.app`, monkeypatch'lenebilir runner/speaker accessor'ları.

- [ ] **Step 1: Failing e2e test yaz**

`brain/tests/test_speaker_e2e.py`:

```python
import json
import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeDB
from app.speaker_store import enroll_anchors

class _OneShotRunner:
    def run_live(self, **kwargs):
        async def events():
            from app.voice import vp  # not used; keep import graph obvious
            class T: text = "merhaba"
            class E:
                content = None; turn_complete = False
                input_transcription = T(); output_transcription = None
            yield E()
        return events()

class _Sessions:
    def __init__(self): self._s = type("S", (), {"state": {}})()
    async def get_session(self, **k): return self._s
    async def create_session(self, **k): return self._s

@pytest.fixture
def wired(monkeypatch):
    import app.main as main
    db = FakeDB()
    enroll_anchors(db, "kadir@example.com", [[1.0, 0.0, 0.0]])
    sessions = _Sessions()
    monkeypatch.setattr(main, "get_voice_runner_sessions_memory",
                        lambda: (_OneShotRunner(), sessions, None))
    # speaker service with a fake embed: any pcm -> matching vector
    from app.speaker import SpeakerService
    svc = SpeakerService(db, embed_fn=lambda pcm: [1.0, 0.0, 0.0], now_fn=lambda: "t",
                         accept=0.35, adapt=0.6, cap=20, top_k=3)
    monkeypatch.setattr(main, "get_speaker_service", lambda: svc)
    monkeypatch.setattr("app.voice.verify_token_email", lambda t: "kadir@example.com")
    return main, sessions

def test_ws_voice_emits_speaker_event_and_sets_locked_trust(wired):
    main, sessions = wired
    client = TestClient(main.app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(json.dumps({"token": "t", "device_hint": "tablet", "presence": "locked"}))
        ws.send_bytes(b"\x00\x01\x00\x01")            # mic audio -> buffered
        # drain events until we see the speaker event
        seen = None
        for _ in range(5):
            msg = ws.receive()
            if "text" in msg and '"speaker"' in msg["text"]:
                seen = json.loads(msg["text"]); break
        assert seen == {"type": "speaker", "role": "user", "verified": True, "score": 1.0}
    from app import config, trust
    assert sessions._s.state[config.TRUST_STATE_KEY] == trust.MEDIUM   # locked+match -> MEDIUM
```

Not: TestClient websocket event sıralaması/`run_live` sürüşü implementasyonda ince ayar gerektirebilir (mic frame'in `_pump_mic_once`'a ulaşması için gönderim sırası). Bu, spec §15'teki "run_live sıralaması" açık ucunun gerçek davranışını **kanıtlayan** testtir; sıralama beklenenden farklıysa bridge'in buffer/verify tetikleme noktası buna göre düzeltilir (fix burada yapılır, hipotez değil kanıt).

- [ ] **Step 2: Testin fail/iterate ettiğini doğrula**

Run: `cd brain && .venv/bin/python -m pytest tests/test_speaker_e2e.py -v`
Expected: önce FAIL/iterate; sıralama netleşince PASS. (Bridge'in verify tetikleme noktası gerekiyorsa Task 9'daki `_verify_utterance` çağrısı ayarlanır.)

- [ ] **Step 3: Enroll yardımcı script**

`brain/scripts/enroll_kadir.py`:

```python
"""Enroll Kadir's voiceprint anchors from local WAV/PCM clips.
Usage: python scripts/enroll_kadir.py <id-token> <base-url> clip1.pcm clip2.pcm ...
Clips must be raw PCM16 mono 16kHz (ffmpeg -ac 1 -ar 16000 -f s16le)."""
import base64, sys, urllib.request, json

def main(argv):
    token, base_url, *paths = argv
    clips = [base64.b64encode(open(p, "rb").read()).decode() for p in paths]
    req = urllib.request.Request(
        f"{base_url}/api/voice/enroll",
        data=json.dumps({"clips": clips}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        print(r.read().decode())

if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 4: Commit**

```bash
git add brain/tests/test_speaker_e2e.py brain/scripts/enroll_kadir.py
git commit -m "test(speaker): WS e2e harness (evt_speaker + locked-trust) + enroll helper script"
```

---

## Task 12: Tam suite + final review + deploy hazırlığı

**Amaç:** Tüm suite yeşil (torch'suz + torch'lu iki koşu), final review, deploy notları. Deploy'un kendisi HITL (Kadir onayı).

**Files:**
- Modify: `brain/README.md` (speaker bölümü)

- [ ] **Step 1: Torch'suz suite (mevcut venv)**

Run: `cd brain && .venv/bin/python -m pytest -q`
Expected: `test_speaker_embed.py` **skipped** (torch yok, `importorskip`), geri kalan hepsi PASS (mevcut 93 + yeni birim testleri).

- [ ] **Step 2: Torch'lu suite (`.venv-speaker`)**

Run: `cd brain && .venv-speaker/bin/python -m pytest -q`
Expected: embed testleri dahil hepsi PASS.

- [ ] **Step 3: README speaker bölümü**

`brain/README.md`'ye ekle: `[speaker]` extra kurulumu, enroll akışı (`scripts/enroll_kadir.py`), eşik env'leri (`JARVIS_SPEAKER_*`), `SPEAKER_MODEL_DIR`, Dockerfile'a torch eklendiğinde imaj ağırlığı + `min-instances=1` notu, deploy komutuna `--set-env-vars`/kaynak sınırları.

- [ ] **Step 4: Deploy hazırlığı — Dockerfile + kaynaklar (deploy HITL)**

`Dockerfile` `pip install .` → `pip install ".[speaker]"` olmalı (torch prod imajına girer). Model dosyaları: build-time indirme vs runtime `/tmp` (cold-start etkisi — spec §15). Bu adım Dockerfile'ı günceller ama **deploy'u Kadir onaylayınca** yapılır; `jarvis-voice` için `--memory` artışı + `--min-instances 1` (sıcak model) gcloud komutuna eklenir. Not olarak README'ye yaz, deploy'u bekletme.

- [ ] **Step 5: Final review (subagent) + commit**

Subagent-driven review (bkz. subagent-driven-development). Bulgular düzeltilir; sonra:

```bash
git add brain/README.md brain/Dockerfile
git commit -m "docs(speaker): README + Dockerfile [speaker] extra + deploy notes (min-instances, weight)"
```

- [ ] **Step 6: SDD ledger + memory güncelle**

`.superpowers/sdd/progress.md`'ye 3a task sonuçlarını, `kadir-ses-kimlik`/`jarvis-durum` memory'lerine "3a implemented" durumunu işle.

---

## Self-review notları (spec kapsamı)

- Spec §5 adaptive galeri → Task 3 (math) + Task 6 (guarded self-feed) + Task 5 (persist). ✓
- Spec §6 trust füzyonu → Task 2. ✓
- Spec §7 politika modülasyonu → Task 7 (default HIGH → text yolu korunur). ✓
- Spec §10 enrollment → Task 10 + Task 11 script. ✓
- Spec §11 protokol → Task 8. ✓
- Spec §9 veri akışı (utterance verify) → Task 9. ✓
- Spec §13 test (birim + model + e2e) → Task 2-11. ✓
- Spec §15 açık uçlar: torch sürüm/ağırlık → Task 1; run_live sıralaması → Task 11 (kanıtla); embedding dim → Task 1; şema → Task 5 (tek-doküman); eşik kalibrasyon → Task 6/7 config. ✓
- Spec §16 kapsam dışı (native/3c/masaüstü/tam diarization) plana **dahil edilmedi**. ✓
