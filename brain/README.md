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
  buffers puts steady-state usage well above that. Start both services at
  **`--memory 2Gi`** as a conservative floor and confirm actual RSS
  empirically after deploy — this number is a reasoned estimate, not a
  measurement (no running container was available to profile in the
  environment this fix was written in; see `fix-wave2-report.md`).
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
  `classifier.ckpt` 5.5 MB, plus three small files); both `/opt/hf-cache` and
  `/root/.cache/huggingface` are absent, so the weights ship once; runtime
  `HF_HOME=/tmp/hf-cache`; and `speaker.embed()` returns a 192-dim vector with
  `HF_HUB_OFFLINE=1` — i.e. the model genuinely loads from the image with no
  network. Measured weight: model **85 MB**, torch **750 MB**, which is where
  the "~850 MB" figure above comes from. **Still a reasoned estimate, not a
  measurement: the `2Gi` memory floor** — that needs RSS from a running
  revision.
- Build context: `brain/.gcloudignore` exists because `gcloud builds submit`
  does not read `.dockerignore` and does not find the repo-root `.gitignore`
  when the source directory is `brain/`. Without it the upload was 2.5 GiB
  (both interpreters); with it, 560 KiB.
