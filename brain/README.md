# JARVIS Brain

FastAPI backend for JARVIS: `/api/chat`, `/api/history`, and the voice
gateway, backed by Firestore for persistent memory and chat transcripts.

## Deploy

The `messages` collection query in `app/messages.py` (`user_id` ==, `session_id`
==, `ts` order_by) requires a Firestore composite index. The index definition
is checked in at `firestore.indexes.json`, but **that file is not auto-applied
by anything** — there is no `firebase.json` and no CI step wired to deploy it.
The index must be created manually, once per Firestore database, before the
query works:

```bash
gcloud firestore indexes composite create --project your-gcp-project --collection-group messages --query-scope COLLECTION --field-config field-path=user_id,order=ascending --field-config field-path=session_id,order=ascending --field-config field-path=ts,order=descending
gcloud firestore indexes composite list --project your-gcp-project   # verify READY before deploy
```

If the index already exists, the create command returns `ALREADY_EXISTS`,
which is not an error. Confirm the index state is `READY` (not `CREATING`)
before deploying a backend revision that depends on it — a missing or
still-building index makes `/api/history` fail with a Firestore
`FAILED_PRECONDITION` (surfaced to clients as the generic 502 infra error).

## LLM proxy + yerel embedding + ses protokolü v2 (30 Temmuz 2026)

"GOOGLE_API_KEY'siz mimari": beyin artık AI Studio API anahtarıyla Gemini
API'sine değil, abonelik-OAuth'lu yerel bir LLM proxy'sine (CLIProxyAPI)
konuşuyor; embedding yerel bir modelde çalışıyor; ses tarafında STT/TTS cihaz
üstüne taşındı.

- **Metin chat — CLIProxyAPI sidecar.** `JARVIS_LLM_BASE_URL` set ise ADK
  `Gemini` instance'ı proxy'nin base_url'ine bağlanır
  (`app/main.py:_build_text_model`; prod'da `http://localhost:8317`, sidecar
  ile aynı instance). Model id'si proxy kataloğundan dinamik çözülür
  (`app/text_model.py` — "her zaman en yeni kullanılabilir flash" kuralı; pin
  yok, yalnızca katalog çözümü başarısız olursa devreye giren bir fallback
  sabiti var: `gemini-3.6-flash-high`). `JARVIS_TEXT_MODEL` set ise bu bir
  mutlak pin'dir (test/acil durum; otomatik çözümü tamamen geçersiz kılar).
  Auth için kod yok: genai SDK `GOOGLE_API_KEY`'i `x-goog-api-key` header'ı
  olarak gönderir — prod'da bu secret'ın **içeriği artık proxy'nin kendi
  api-key'idir** (Google'ın değil).
- **Bellek embedding'i — yerel e5.** `gemini-embedding-001` yerine
  `intfloat/multilingual-e5-base` (sentence-transformers, 768-dim, lazy
  singleton; `app/memory.py`). E5 prefix kuralı: saklanan metin
  `embed_passage`, arama metni `embed_query` ile gömülür — ikisini takas
  etmek sessiz yanlış sonuç demektir. Model imaja build-time'da
  `/opt/hf-cache` altında baked edilir (Dockerfile; runtime
  `HF_HOME=/opt/hf-cache`). Eski `gemini-embedding-001` vektörleriyle
  uyumsuzdur — migrasyon: `python scripts/reembed_e5.py`.
- **Ses — protokol v2, Gemini Live kaldırıldı.** `app/live_model.py` silindi;
  sunucu artık hiç Gemini Live oturumu açmıyor. Android istemci cihaz-üstü
  `SpeechRecognizer` (tr-TR) ile konuşmayı metne çevirip `user_text` frame'i
  gönderir; `speech_start` frame'i utterance onset'ini işaretler. Sunucu turu
  metin runner'ıyla koşturup `jarvis_text` event'i döner, cihaz bunu kendi
  `TextToSpeech`'iyle seslendirir. PCM yalnızca speaker-ID (ECAPA) için
  sunucuya akar. WS hello'da `client_caps` zorunludur; `client_caps`
  gönderemeyen eski (v1) istemciye `evt_error("Uygulamayı güncelle")` +
  close **4409** döner (bkz. `app/voice.py`, `app/voice_protocol.py`). PWA
  (`web/`) v1'de kaldığı için ses yolu artık desteklenmiyor.

### Operatör notları (secrets + deploy)

- **Secret'lar (Secret Manager):**
  - `cliproxy-oauth-antigravity` — CLIProxyAPI'nin Google OAuth token dosyası
    (`antigravity-<hesap>.json`); sidecar'a read-only volume olarak mount
    edilir, entrypoint her start'ta writable auth-dir'e kopyalar
    (`proxy/entrypoint.sh`).
  - `cliproxy-api-key` — proxy'nin kendi api-key'i; hem sidecar'a
    (`CLIPROXY_API_KEY`) hem brain konteynerine (`GOOGLE_API_KEY` olarak)
    verilir.
  - `gemini-api-key` — eski AI Studio anahtarı; geri dönüş sigortası olarak
    bir hafta saklı tutulur, sonra silinebilir.
- **Sidecar deploy akışı:** proxy imajı `brain/proxy/Dockerfile`'dan
  (`gcr.io/your-gcp-project/jarvis-llm-proxy:v7.2.111`); servis tanımları
  `brain/deploy/jarvis-brain.yaml` ve `brain/deploy/jarvis-voice.yaml`
  (multi-container: `brain` + `llm-proxy`, `containerDependencies` ile start
  sırası). Deploy artık **`gcloud run deploy --source` ile değil**,
  declarative YAML ile yapılır:
  ```bash
  gcloud run services replace brain/deploy/jarvis-brain.yaml --region europe-west1
  gcloud run services replace brain/deploy/jarvis-voice.yaml --region europe-west1
  ```
- **OAuth token re-seed (token yenilendiğinde / hesap değişiminde):** yerelde
  CLIProxyAPI login'i `~/.cli-proxy-api/antigravity-<hesap>.json` üretir;
  bunu secret'a yeni versiyon olarak ekleyin:
  ```bash
  gcloud secrets versions add cliproxy-oauth-antigravity \
      --data-file="$HOME/.cli-proxy-api/antigravity-<hesap>.json" \
      --project your-gcp-project
  ```
  Yeni versiyonun alınması için servislerin yeni revision'a geçmesi gerekir
  (secret volume'ları revision başına çözülür) — `services replace` ile
  aynı YAML'ı yeniden uygulamak yeterli.
- **Superseded — eski AI Studio kurulumu:** `docs/superpowers/plans/`
  altındaki Katman 1/2a plan belgelerindeki "AI Studio API anahtarı üret →
  `gemini-api-key` secret'ına yaz → `GOOGLE_API_KEY` olarak servise bağla"
  talimatları artık **geçerli değildir** (ilgili plan belgesinin başına
  superseded notu eklendi). Tarihsel kayıt olarak duruyorlar; yeni kurulum
  bu bölümdeki akıştır.

## Speaker identity (Katman 2b Dilim 3a — Kadir ses-kimliği)

Voice-mode utterances are verified against Kadir's enrolled voiceprint: an
ECAPA-TDNN speaker embedding (`app/speaker.py`) scored by cosine similarity
against a small gallery of anchor + adaptive vectors persisted per-user in
Firestore (`app/speaker_store.py`). This is an **optional extra** — none of
it is imported unless `[speaker]` is installed and `app/speaker.embed()` is
actually called.

### Installing `[speaker]` — CPU-only torch is required on Linux

```bash
pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchaudio>=2.2"
pip install -e ".[speaker]"
```

**Do not** run a plain `pip install -e ".[speaker]"` on Linux: PyPI's default
`torch` wheel resolves to the CUDA-dependent build (pulls in
`nvidia-cublas`, `nvidia-cudnn`, a CUDA toolkit, etc. — several GB). Cloud Run
(`jarvis-voice`) is CPU-only, so the CPU wheel index must be pinned first —
the command above installs `torch`/`torchaudio` as their `+cpu` variant
*before* `[speaker]` is installed, so the second command finds those
constraints already satisfied and only pulls in `speechbrain` + its
(small) transitive dependencies.

Measured weight, byte-accurate (`du -sbL`, so HF hub cache symlinks are
followed rather than under-counted):

| Component | Size |
|---|---|
| `torch` (CPU-only) | ~747 MB |
| `torchaudio` (CPU-only) | ~2.5 MB |
| `speechbrain` (package code) | ~7.7 MB |
| ECAPA model weights (`spkrec-ecapa-voxceleb`) | ~89 MB |
| **Total (core speaker stack)** | **~846 MB** |

### Two interpreters

- **`.venv`** — Python 3.14, torch-free. Runs the bulk of the suite; the
  ECAPA embed tests (`tests/test_speaker_embed.py`) SKIP here via
  `pytest.importorskip("torch")`.
- **`.venv-speaker`** — Python 3.12, torch-cpu, matching the Dockerfile's
  `python:3.12-slim`. Runs the FULL suite, including the ECAPA embed tests.
  Create it with:
  ```bash
  cd brain && uv venv --python 3.12 .venv-speaker
  .venv-speaker/bin/pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchaudio>=2.2"
  .venv-speaker/bin/pip install -e ".[speaker,dev]"
  ```

Run the full suite in each: `.venv/bin/python -m pytest -q` (torch-free) and
`.venv-speaker/bin/python -m pytest -q` (full, incl. embed tests) — both must
be green before a speaker-related change is considered done.

### Enrollment

Bootstrap Kadir's voiceprint anchors via the authenticated `/api/voice/enroll`
endpoint, using the helper script:

```bash
python scripts/enroll_kadir.py <id-token> <base-url> clip1.pcm clip2.pcm ...
```

Clips must be raw PCM16 mono 16 kHz with no container/header:

```bash
ffmpeg -i input.wav -ac 1 -ar 16000 -f s16le clip1.pcm
```

Enrollment is additive across calls (anchors accumulate, never overwritten)
and requires the same Google ID token / allowlist auth as `/api/chat`.

### Tunable env vars

| Variable | Default | Meaning |
|---|---|---|
| `JARVIS_SPEAKER_ACCEPT` | `0.35` | Cosine threshold above which an utterance counts as verified |
| `JARVIS_SPEAKER_ADAPT` | `0.60` | Cosine threshold above which a verified utterance also self-feeds the adaptive gallery (only when authed as Kadir). Scored against the **immutable anchors only**, never the full gallery — see note below |
| `JARVIS_SPEAKER_TOPK` | `3` | Number of top gallery similarities averaged into the score |
| `JARVIS_SPEAKER_ADAPTIVE_CAP` | `20` | Max adaptive samples kept; once over cap the **most redundant** sample is evicted (nearest-neighbour), not the oldest |
| `JARVIS_SPEAKER_UTTERANCE_SECONDS` | `10` | Rolling mic-buffer window kept for the next verification. A value that rounds down to ≤ 0 bytes is refused with a warning and falls back to the default (it would silently remove the bound, not disable it) |
| `JARVIS_SPEAKER_MIN_UTTERANCE_SECONDS` | `0.5` | Minimum buffered audio the **turn_complete fallback** will score. Below it the turn is left unverified rather than scored on a fragment — see note below. Negative values fall back to the default; a value above the mic window is clamped to it (a floor no buffer can reach would silently retire the fallback). An explicit `0` is a deliberate opt-out of the guard |
| `SPEAKER_MODEL_DIR` | `/tmp/spkrec-ecapa` (code default; the Dockerfile overrides this to `/opt/spkrec-ecapa`, where the model is baked in at build time — see Deploy notes below) | ECAPA model dir |

**Where verification runs, and the one-sided floor.** A turn is verified once,
at the finished input transcription — Gemini telling us the utterance is
complete. There is also a fallback at `turn_complete`, because ADK itself warns
that a finished-transcription signal may never arrive. That fallback is the
only path with no positive signal that the buffer holds speech: the mic stays
open for the whole turn (the PWA has no VAD gate), so between the verification
and `turn_complete` the drained buffer refills with room noise. The floor above
applies **only** to the fallback. An unverified result is not neutral — it
fuses to LOW under `locked`/`ambient` — so scoring noise would actively lock
Kadir out. Below the floor nothing is published and the per-connection baseline
stands, which is never HIGH for those presence modes. The transcription path
deliberately has no floor, so short commands ("evet", "kapat") are still
verified.

`turn_complete` also **drains** the mic buffer. Without that, everything the
open mic collected while the model was speaking — room noise and silence; the
PWA requests `echoCancellation`, so the assistant's own output is mostly but
not wholly suppressed — survived as a prefix of the next utterance, and
`speaker.embed` averages the whole buffer into one embedding with no VAD or
trimming. On a barge-in (`interrupted`) the buffer instead holds an utterance
that has *started but not ended*, so it is neither scored nor dropped; it is
trimmed to its onset window (the same `MIN_UTTERANCE` length) to strip the
model's speaking time from in front of it, and verified at its own boundary.

That "pending utterance" claim is settled only by the verification that
consumes the buffer — never by a later event. Every earlier version of this
logic keyed a per-turn flag on an event that is not guaranteed to arrive, and
each time the flag leaked into the following turn.

The bounded cost of that rule is pinned by
`test_the_pending_claim_survives_a_turn_with_no_transcription_at_all`: while a
claim is outstanding, a turn that produces *no* input transcription at all is
neither verified nor drained. Three consequences, stated rather than implied:

- that turn keeps the previously **measured** level — never a fabricated HIGH,
  and the connection baseline is already MEDIUM under `locked`/`ambient`;
- because the drain is skipped too, audio accumulates across such turns. It
  stays bounded by the rolling mic window, so the worst case is one 10-second
  embedding spanning them, not unbounded growth;
- a tool call can land inside that window (ADK notes at
  `gemini_llm_connection.py:280-282` that `tool_call` may arrive before its
  transcription). It is evaluated at the last measured level, for the same
  authenticated user on the same connection.

The claim is also set by a barge-in that lands on an already-empty buffer, so
"a barge-in happened" is the real precondition rather than "an utterance is
genuinely pending" — the second condition (a turn with zero transcriptions) is
what actually gates the residual's frequency. The real fix for the whole class
is an energy/VAD gate, not another flag.

Empirically measured separation on the committed fixtures (`tests/fixtures/`,
real LibriSpeech clips, see `tests/fixtures/README.md`): same-speaker cosine
**0.7251**, different-speaker **0.1603** — a ~0.56 margin, which is why the
defaults above (`accept=0.35`, `adapt=0.60`) sit where they do: comfortably
below the observed same-speaker match and above the observed impostor score.

**Two thresholds, two galleries.** `ACCEPT` is scored against the whole gallery
(anchors ∪ adaptive) — that is the point of a gallery: it spans days, health and
devices. `ADAPT` is scored against the **anchors only** (`SpeakerProfile.anchor_score`),
because gating self-feeding on the full gallery is a poisoning ratchet: one
adaptive sample that slipped in dominates its own top-k and pushes every later
attempt further above the gate. Practical consequence for calibration: right
after enrollment there are only 1–3 anchors, so a `0.60` gate against them is
materially stricter than the same number against a grown gallery — expect
self-feeding to start slowly and tune `JARVIS_SPEAKER_ADAPT` against measured
anchor scores, not full-gallery scores.

**Limitation — voice is a risk signal, not a hard second factor.** `presence`
(`foreground` / `locked` / `ambient`) is asserted by the client in its WS hello
and is **not verifiable server-side**, and `trust.assess` returns `HIGH`
unconditionally for `foreground`. So anyone holding Kadir's Google ID token can
simply send `presence: "foreground"` and get unconditional `HIGH` — voice
verification is bypassed entirely, without needing to defeat the voiceprint.
Speaker identity therefore raises the cost of misuse and annotates the audit
trail; it does **not** gate access on its own. Treat the ID token as the
security boundary. Making voice a real second factor would require a
server-verifiable presence/attestation signal, which this slice does not have.

### Deploy notes (for when the owner approves — not executed by this repo)

- **Multi-container reality (30 Tem 2026):** both services are now
  two-container deployments (`brain` + `llm-proxy` sidecar) defined
  declaratively in `brain/deploy/jarvis-brain.yaml` and
  `brain/deploy/jarvis-voice.yaml`. Deploy is **`gcloud run services replace
  brain/deploy/<servis>.yaml --region europe-west1`** — the older
  `gcloud run deploy --source ...` flow is gone (it cannot express
  sidecars/secret volumes). See the "LLM proxy + yerel embedding + ses
  protokolü v2" section above for secrets and the token re-seed procedure.
- **The ECAPA model is baked into the image at build time**
  (`SPEAKER_MODEL_DIR=/opt/spkrec-ecapa`, set in the Dockerfile), not
  downloaded on first request. An earlier draft of this section recommended
  pointing `SPEAKER_MODEL_DIR` at "a persistent path" instead of the default
  `/tmp` — that was never actionable on Cloud Run without a volume mount
  (GCS FUSE / Filestore), which this project does not provision, so the
  advice was misleading. It is now moot: the Dockerfile downloads the model
  once during `docker build`, dereferences speechbrain's HuggingFace-cache
  symlinks into real files (kept out of the non-root `appuser`'s reach
  otherwise — see Dockerfile comments), and ships the ~89 MB of weights
  inside the image, owned by `appuser`. This removes the runtime
  HuggingFace fetch entirely: no cold-start download latency, no tmpfs
  (RAM) charge against instance memory for the weights (Cloud Run's `/tmp`
  is backed by memory, not disk), and no risk of an unauthenticated HF
  rate-limit failing a live request.
- **Both `jarvis-brain` and `jarvis-voice` now carry the ~850 MB
  torch/speechbrain stack, not just `jarvis-voice`.** Both services deploy
  from this same `brain/` source and this one Dockerfile. Decision: keep a
  single shared image rather than splitting it per service. Reasoning —
  `POST /api/voice/enroll` lives in the same FastAPI app and calls
  `speaker.embed()` directly; `scripts/enroll_kadir.py` takes an arbitrary
  `base_url`, and the already-deployed clients (Android app, web/PWA) are
  wired to `jarvis-brain`'s URL, so enrollment plausibly runs through
  `jarvis-brain`, not only `jarvis-voice`. Splitting the image (e.g. a
  build arg that skips the `[speaker]` extra for the text service) would
  only be safe if `jarvis-brain` could never receive an enroll call, which
  cannot be guaranteed without an application-code change (moving or
  conditionally disabling the endpoint per service) — out of scope for this
  fix wave. **Consequence: raise `--memory` for BOTH services**, not just
  `jarvis-voice` as earlier notes assumed — the `512Mi` used at first deploy
  predates torch entirely and will not be sufficient for either service now:
  `torch`'s own import footprint plus the loaded model plus request-time
  buffers puts steady-state usage well above that.

  **Now measured, inside the real image** (`resource.ru_maxrss`, cumulative
  peak): bare interpreter **17.9 MB** → `app.main` imported **126.0 MB** →
  `speaker` module imported **126.0 MB** (unchanged: torch is genuinely lazy,
  a text-only instance never pays for it) → ECAPA loaded plus one 3 s embed
  **562.2 MB** → five further full-window (10 s) embeds **590.4 MB**.

  So **peak ≈ 590 MB**, and `512Mi` would indeed OOM on the first embed. The
  measured floor is **`--memory 1Gi`** (~70 % headroom over peak); the model is
  a process-wide singleton and each connection's mic buffer is only 320 KB, so
  concurrency barely moves this. `2Gi` remains a defensible conservative
  choice, but it is now a deliberate margin rather than a guess — and on
  `jarvis-voice`, which runs `--min-instances 1`, that margin is billed
  continuously.
- `jarvis-voice` additionally wants `--min-instances 1` (keeps the model
  warm — a cold-start mid voice-session is bad UX). `jarvis-brain` can stay
  at `--min-instances 0` since a text-chat cold start is more tolerable, but
  it still needs the same `--memory` floor as `jarvis-voice` now that it
  carries the same dependency stack.
- **The image has now actually been built and inspected** (Cloud Build, 4m03s,
  `speaker-build-check:wave7`), which earlier waves could only reason about
  statically. Verified by running the checks *inside* the built image:
  `uid=1000(appuser)` non-root; **torch 2.13.0+cpu** (the CPU wheel resolved,
  not the multi-GB CUDA one); `/opt/spkrec-ecapa` holds **regular files, zero
  symlinks**, all owned by `appuser` (`embedding_model.ckpt` 83.3 MB,
  `classifier.ckpt` 5.5 MB, plus three small files); at that inspection both
  `/opt/hf-cache` and `/root/.cache/huggingface` were absent, so the weights
  shipped once; and `speaker.embed()` returns a 192-dim vector with
  `HF_HUB_OFFLINE=1` — i.e. the model genuinely loads from the image with no
  network. **Update (30 Tem 2026, e5 bake):** runtime `HF_HOME` is now
  `/opt/hf-cache`, not `/tmp/hf-cache` — a later Dockerfile layer bakes the
  multilingual-e5-base memory embedder (~1.1 GB) into that path and the
  runtime `ENV HF_HOME` points there (ECAPA is untouched: it still loads from
  `SPEAKER_MODEL_DIR=/opt/spkrec-ecapa`). Measured weight: model **85 MB**,
  torch **750 MB**, which is where the "~850 MB" figure above comes from.
  **Still a reasoned estimate, not a measurement: the `2Gi` memory floor** —
  that needs RSS from a running revision. (Note: the deployed
  `brain/deploy/*.yaml` now sets 3Gi, because the e5 embedder adds ~1.1 GB
  weights + encode buffers in the same process — see the service YAML
  comment.)
- Build context: `brain/.gcloudignore` exists because `gcloud builds submit`
  does not read `.dockerignore` and does not find the repo-root `.gitignore`
  when the source directory is `brain/`. Without it the upload was 2.5 GiB
  (both interpreters); with it, 560 KiB.
- **A probe revision is already deployed and serving no traffic.**
  `jarvis-brain-00007-vix`, built from merged `main`, reachable at
  `https://probe---jarvis-brain-xxxxxxxxxx-ew.a.run.app` (`/api/health` → 200,
  `/` → 200; `/healthz` → 404, which is Google Frontend reserving that path,
  not a fault). Production traffic stays on `jarvis-brain-00006-222`. To
  promote it:

  ```bash
  gcloud run services update-traffic jarvis-brain --region europe-west1 \
      --to-revisions jarvis-brain-00007-vix=100
  # rollback: --to-revisions jarvis-brain-00006-222=100
  ```

  Do the promotion **together with** enrolling Kadir's voice
  (`scripts/enroll_kadir.py`): until the gallery has anchors every utterance
  scores 0, which is harmless under `foreground` (HIGH regardless) but means
  `locked`/`ambient` sit at MEDIUM/LOW. `jarvis-voice` still needs its own
  deploy with `--min-instances 1`.

### Ses kimliği yönetimi (Dilim 3d)

`app/voice_manage.py` adds six `require_user`-gated endpoints (mounted under
`app.main`) so Kadir can see, correct and delete his own voiceprint gallery
and verification history. Every one runs off the event loop via
`asyncio.to_thread` and goes through `SpeakerService` — the same gallery lock
`identify()`/`enroll()` use — so a management call can never interleave with
a live verification and drop one side's write.

| Endpoint | Does |
|---|---|
| `GET /api/voice/profile` | Anchors + adaptive samples + verification history + `quality_indicators` (mean score, fail rate, per-device/per-label breakdown, last-10 vs previous-10 trend), no vectors |
| `PATCH /api/voice/sample/{id}` | Sets `label`/`note` on one sample; `label` must be in the fixed set or absent field leaves it untouched, `label: null` clears it |
| `DELETE /api/voice/sample/{id}` | Deletes one anchor or adaptive sample; refuses (400) to delete the last anchor — an anchorless profile cannot score or referee ADAPT |
| `POST /api/voice/history/{id}/confirm` | "Bu bendim": promotes the entry's stored embedding into a MANUAL adaptive sample (votes in ACCEPT, never referees ADAPT); idempotent, capped by `JARVIS_SPEAKER_MANUAL_CAP` |
| `POST /api/voice/history/{id}/reject` | "Bu ben değildim": marks the entry and removes the sample it fed the gallery with, if any; idempotent |
| `DELETE /api/voice/profile` | Deletes the gallery AND the verification history together — no half-deletion |

**History model.** Verification history is a per-user ring buffer
(`speaker_history/{user_id}`, oldest-first): every identify call appends one
entry and `record()` trims to the newest `JARVIS_SPEAKER_HISTORY_CAP` (default
50). Entries carry the utterance's **embedding, not audio** — the vector is
what lets a later confirm/reject correction feed or prune the gallery, and it
cannot be inverted back into listenable audio; storing audio would be a
categorically different privacy liability and is out of scope. A confirm/reject
sets the entry's `correction` field (`"confirmed"` / `"rejected"`), which is
also what makes both endpoints idempotent — a repeat call returns
`already: true` instead of double-applying.

| Variable | Default | Meaning |
|---|---|---|
| `JARVIS_SPEAKER_HISTORY_CAP` | `50` | Verification history ring-buffer size per user |
| `JARVIS_SPEAKER_MANUAL_CAP` | `5` | Max manually-confirmed samples in the adaptive gallery; an accident guard, not a security boundary — the real guarantee is revocability |

**Label set** (`app/config.py: SPEAKER_SAMPLE_LABELS`, fixed vocabulary so
scores aggregate meaningfully): `saglikli`, `hasta`, `yorgun`, `gurultulu`,
`kulaklik`, `hoparlor`, `arac`. The free-text `note` field on a sample catches
whatever the fixed set misses.

**Privacy (spec §6, §7).** Two claims, load-bearing enough to state exactly:
vectors never leave the server (every response is built by explicit allowlist
projection, and tests pin the absence of `vec` anywhere in any response body);
and no "biyometri yaptım" header exists — the client biometric gate is
Android-side UX, the server never trusts it (spec §7). The Android biometric
prompt is tracked as a separate future plan (3d-3), not part of this slice.

## GitHub repo takibi (repo-watch)

Jarvis, Kadir'in ilginç bulduğu GitHub repo'larını saatlik kontrol eder; yeni
release ve default-branch commit'lerini olay olarak biriktirir ve sohbet
başında Türkçe özetler. Poller `app/repo_watch.py`, ajan araçları
(`watch_repo` / `unwatch_repo` / `list_watched_repos` / `get_repo_updates`)
`app/tools.py`'de, scheduler ucu `POST /api/jobs/repo-watch` olarak
`app/main.py`'de.

**Firestore koleksiyonları** (tekil, kullanıcı bazlı değil):

- `repo_watch` (doc id = `owner/repo`): `note`, `added_at`, poller durumu
  (`last_check`, `last_error`, `release_etag`, `commits_etag`,
  `last_release_tag`, `last_commit_sha`). Yeni eklenen repo ilk turda olay
  ÜRETMEZ — mevcut durum baseline olur.
- `repo_watch_events` (auto-id): `repo`, `kind` (`release`/`commits`),
  `title`, `detail`, `url`, `ts`, `surfaced`. Olaylar silinmez; `surfaced`
  "Kadir'e gösterildi" demektir.

**Kota:** her uç koşullu istek (ETag/`If-None-Match`) atar — değişmeyen repo
304 döner ve 0 kota harcar. `GITHUB_TOKEN` opsiyoneldir; yoksa anonim kota
(60 istek/saat/IP, ~10 repo için yeterli).

| Variable | Default | Meaning |
|---|---|---|
| `GITHUB_TOKEN` | _(boş)_ | GitHub API token'ı (opsiyonel; Secret Manager'dan). Yoksa anonim kota |
| `JARVIS_SCHEDULER_SA` | _(boş)_ | Scheduler job'unun OIDC service account e-postası; boşsa uç 503 döner |
| `JARVIS_SCHEDULER_AUD` | _(boş)_ | OIDC token audience'ı (servis URL'i, örn. `https://<brain-url>`) |

**Scheduler kurulumu** (deploy sonrası, ayrı onayla — spec §6):

```bash
gcloud scheduler jobs create http repo-watch \
  --schedule="17 * * * *" --time-zone=Europe/Istanbul \
  --uri="https://<brain-url>/api/jobs/repo-watch" --http-method=POST \
  --oidc-service-account-email="$JARVIS_SCHEDULER_SA" \
  --oidc-token-audience="https://<brain-url>"
```

## Onay merkezi (Faz Y3)

Kırmızı bölge artık bir çıkmaz sokak değil. Bir kırmızı araç çağrısı
`policy.make_policy_callback`'in `approval_sink`'inden geçip **bekleyen bir
onaya** dönüşür; araç çalışmaz — çalışma anı **onay anıdır**
(`approvals.decide`). North Star §4.8 / §9.

**Koleksiyonlar**

- `approvals` (auto-id): `{user_id, kind, title, detail, tool_name, tool_args,
  zone, session_id, status, created_at, expires_at, decided_at, decided_by,
  outcome}`. `status` ∈ `pending|approved|rejected|expired|failed`.
- `approval_claims` (doc id = onay id'si): kararın **birincil kaydı**
  (`{decision, by, at}`). Onay dokümanındaki `status` bunun izdüşümüdür.

**Neden ayrı bir claim dokümanı.** Firestore'da işlemsiz koşullu güncelleme
yoktur, ama `DocumentReference.create()` atomik bir "yoksa yaz"dır. Çift
dokunuş ya da "push + kuyruk senkronu aynı anda" bu sayede kırmızı bir eylemi
**iki kez çalıştıramaz**; ikinci çağrı `AlreadyExists` alır ve
`{already: true}` döner. (Aynı desen `conversations` başlık yarışında da
kullanılıyor.)

**Zaman aşımı = reddet, ve KARAR anında uygulanır.** Süpürücü iş
(`/api/jobs/approvals-tick`) tek başına yeterli değildir: süpürme ile son
kullanma arasındaki pencerede gelen bir onay, süresi geçmiş bir kırmızı eylemi
çalıştırırdı. `decide()` bu yüzden önce süreyi kontrol eder. Süpürücü bir
temizlik yoludur, güvenlik sınırı değil.

**Yürütme bir allowlist'tir.** `approvals.EXECUTORS` (`register_executor` ile
doldurulur) kayıtlı olmayan bir `tool_name`'i **çalıştırmaz** —
`status=failed`. Onay kaydına keyfi bir isim yazmak kod çalıştırma yolu
değildir.

**Uçlar**

| Uç | Auth | İş |
|---|---|---|
| `GET /api/approvals` | `require_user` | Bekleyenler (süresi geçmişler hariç, ≤50) |
| `GET /api/approvals/{id}` | `require_user` | Tek onayın güncel durumu (kart yenilemesi) |
| `POST /api/approvals/{id}/approve` | `require_user` | Karar + yürütme |
| `POST /api/approvals/{id}/reject` | `require_user` | Karar; yürütme yok |
| `POST /api/jobs/approvals-tick` | `require_scheduler` | Süresi geçenleri `expired` yapar |

Başkasının onayı **404** döner (403 değil — başka birinin onayının varlığı bile
sızmasın).

**İlk gerçek kırmızı araç: `cancel_reminder`.** Y3'ten önce `TOOL_ZONES` her
aracı açıkça yeşil/sarı yapıyordu; `DEFAULT_ZONE = red` yalnızca tabloda
olmayan araçlar içindi, ki öyle bir araç yoktu — yani onaylanacak hiçbir şey
olmadan mekanizma doğrulanamazdı. `cancel_reminder` §9'un "bir şey silme"
örneğinin en zararsızı ve model onu bugün zaten zincirleyebiliyor
(`list_reminders` id veriyor). Bölge ataması bilinçli olarak muhafazakâr ve
konfigürasyondur (§9: eşikler kodda gevşetilir, sohbette değil).

**Kart sohbettedir** (§4.8: "ayrı ekran değil"). Sink, transcript'e
`kind="approval"` + `meta.approval_id` taşıyan bir model mesajı yazar;
`messages.history()` bu iki alanı yalnızca doluysa döndürür, yani alansız eski
satırlar ve eski istemciler aynen çalışır. Kartın DURUMU transcript'e gömülü
değildir — tek gerçek kaynak onay dokümanıdır.

**Push.** `fcm.dispatch` tek yoldur; `send_reminder` (Y2.4) ve `send_approval`
(Y3) onun sarmalayıcılarıdır. `send_approval` cihaz token'ı yokken sohbete
**yazmaz** (`fallback_text=None`): kartı sink zaten aynı oturuma yazdı, ikinci
bir satır gürültü olurdu. Push kaçarsa onay kaybolmaz — kuyruk uygulama
açılınca `GET /api/approvals` ile senkronlanır.

> **3 Ağustos 2026 saha notu:** Bu tarihe kadar push **hiç çalışmamıştı.**
> Üretimde `fcm_tokens` boştu ve her gönderim sessizce sohbet fallback'ine
> düşüyordu, çünkü Android modülünde `firebase-messaging` bağımlılığı ve
> `FirebaseMessagingService` yoktu. Android istemcisi eklendikten sonra
> doğrulandı: `fcm_tokens` 0 → 1 doküman, gerçek push cihazda
> `channel=jarvis_push` bildirimi olarak çizildi.
