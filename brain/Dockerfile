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
# HuggingFace hub cache (~/.cache/huggingface -> root's home in this build
# stage), not real files -- confirmed by inspecting an actual download.
# Left as symlinks, they would dangle for the non-root `appuser` this image
# switches to below, because /root is not traversable by other users.
# `cp -rL` dereferences every symlink into a real regular file so the
# directory is fully self-contained; the original HF cache is then removed
# so the image doesn't carry the same ~89 MB twice.
ENV SPEAKER_MODEL_DIR=/opt/spkrec-ecapa
RUN python -c "from speechbrain.inference.speaker import EncoderClassifier; EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='${SPEAKER_MODEL_DIR}', run_opts={'device': 'cpu'})" \
    && cp -rL "${SPEAKER_MODEL_DIR}" "${SPEAKER_MODEL_DIR}.real" \
    && rm -rf "${SPEAKER_MODEL_DIR}" "${HOME}/.cache/huggingface" \
    && mv "${SPEAKER_MODEL_DIR}.real" "${SPEAKER_MODEL_DIR}" \
    && chown -R appuser:appuser "${SPEAKER_MODEL_DIR}"

USER appuser
ENV PORT=8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
