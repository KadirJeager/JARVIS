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
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchaudio>=2.2" \
    && pip install --no-cache-dir ".[speaker]" \
    && useradd --create-home appuser
USER appuser
ENV PORT=8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
