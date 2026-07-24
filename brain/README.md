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
| `JARVIS_SPEAKER_ADAPT` | `0.60` | Cosine threshold above which a verified utterance also self-feeds the adaptive gallery (only when authed as Kadir) |
| `JARVIS_SPEAKER_TOPK` | `3` | Number of top gallery similarities averaged into the score |
| `JARVIS_SPEAKER_ADAPTIVE_CAP` | `20` | Max adaptive samples kept; oldest evicted first once over cap |
| `SPEAKER_MODEL_DIR` | `/tmp/spkrec-ecapa` | ECAPA model cache dir — see Deploy notes below |

Empirically measured separation on the committed fixtures (`tests/fixtures/`,
real LibriSpeech clips, see `tests/fixtures/README.md`): same-speaker cosine
**0.7251**, different-speaker **0.1603** — a ~0.56 margin, which is why the
defaults above (`accept=0.35`, `adapt=0.60`) sit where they do: comfortably
below the observed same-speaker match and above the observed impostor score.

### Deploy notes (for when the owner approves — not executed by this repo)

- `SPEAKER_MODEL_DIR` should point at a persistent path, not the default
  `/tmp` (ephemeral on Cloud Run) — otherwise the ~89 MB ECAPA model
  re-downloads on every cold start.
- `jarvis-voice` wants `--min-instances 1` (keeps the model warm — a
  cold-start mid voice-session is bad UX) and a raised `--memory` so torch +
  the loaded model fit comfortably alongside the rest of the process.
