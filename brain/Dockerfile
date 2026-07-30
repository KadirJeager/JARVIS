FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY web ./web
# PyPI's default torch wheel is CUDA-dependent (multi-GB); Cloud Run is
# CPU-only, so the CPU wheel index is pinned FIRST (see brain/README.md
# "Speaker identity" -> "Installing [speaker]", measured ~846 MB this way).
# The second install then finds torch/torchaudio already satisfying
# pyproject's >=2.2 pin and only adds speechbrain + the rest of the base deps.
#
# Both jarvis-brain and jarvis-voice deploy from this one image (see
# README.md "Deploy notes" for why the text service also needs torch: it
# shares this FastAPI app with `POST /api/voice/enroll`).
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchaudio>=2.2" \
    && pip install --no-cache-dir ".[speaker]" \
    && useradd --create-home appuser

# Bake the ECAPA speaker model into the image at BUILD time instead of
# fetching it from HuggingFace inside the first live request: Cloud Run's
# /tmp is a tmpfs (RAM-backed), so a runtime download would charge the
# ~89 MB weights against instance memory on every cold start, add
# unbounded latency to whichever request triggers it, and fail outright on
# an HF rate-limit. This RUN is a separate layer from the pip install above
# so edits here never invalidate that (multi-minute) layer's cache.
#
# speechbrain's from_hparams() populates savedir with SYMLINKS into the
# HuggingFace hub cache, not real files -- confirmed by inspecting an actual
# download. Left as symlinks, they would dangle for the non-root `appuser` this
# image switches to below, because the cache owner's home is not traversable by
# other users. `cp -rL` dereferences every symlink into a real regular file so
# the directory is fully self-contained; the original HF cache is then removed
# so the image doesn't carry the same ~89 MB twice.
#
# HF_HOME pins that cache to an explicit path instead of relying on ${HOME}
# inside RUN: HOME is not guaranteed to be set in a build environment, and
# `rm -rf "${HOME}/.cache/huggingface"` would then expand to
# `rm -rf /.cache/huggingface` -- a silent no-op that ships the 89 MB twice.
# huggingface_hub reads HF_HOME and derives its hub cache from it
# (huggingface_hub/constants.py:168-177: HF_HOME -> default_cache_path =
# HF_HOME/hub), verified in the installed package.
ENV SPEAKER_MODEL_DIR=/opt/spkrec-ecapa
ENV HF_HOME=/opt/hf-cache
RUN python -c "from speechbrain.inference.speaker import EncoderClassifier; EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='${SPEAKER_MODEL_DIR}', run_opts={'device': 'cpu'})" \
    && cp -rL "${SPEAKER_MODEL_DIR}" "${SPEAKER_MODEL_DIR}.real" \
    && rm -rf "${SPEAKER_MODEL_DIR}" /opt/hf-cache /root/.cache/huggingface \
    && mv "${SPEAKER_MODEL_DIR}.real" "${SPEAKER_MODEL_DIR}" \
    && chown -R appuser:appuser "${SPEAKER_MODEL_DIR}"

# Bake the multilingual-e5-base memory embedding model (~1.1 GB) into the
# image at BUILD time, for the same reasons the ECAPA layer above exists:
# a runtime download would charge the weights against tmpfs RAM on every
# cold start, add unbounded latency to the first memory write/search, and
# fail outright on an HF rate-limit. Unlike ECAPA, sentence-transformers
# reads the hub cache DIRECTLY (no savedir copy step), so the cache itself
# is the baked artifact: this RUN downloads into HF_HOME=/opt/hf-cache
# (still set from above, after the ECAPA layer emptied it) and the runtime
# ENV below points HF_HOME at the same path. Separate layer so edits here
# never invalidate the ECAPA layer's cache.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-base')" \
    && chown -R appuser:appuser /opt/hf-cache

# Re-point HF_HOME for RUNTIME -- at the e5 cache baked above, NOT the
# build-time-deleted /tmp fallback the ECAPA-only image used. speechbrain
# never touches HF at runtime (SPEAKER_MODEL_DIR is self-contained), and
# sentence-transformers finds the complete e5 snapshot in the hub cache,
# so nothing downloads in the running container. The directory is read-only
# for appuser in practice: both models are fully baked, so a cache write
# would only ever be a (loud, debuggable) fallback, never the happy path.
ENV HF_HOME=/opt/hf-cache

USER appuser
ENV PORT=8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
