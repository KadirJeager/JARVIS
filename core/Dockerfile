# JARVIS core service image: the PWA is built from core/web in a Node stage
# and served by the Python service. Dependencies come from package-lock.json
# and uv.lock only.
FROM node:24-slim AS web
WORKDIR /web
# Dependencies first so source edits reuse this layer.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
COPY src ./src
RUN uv sync --locked
COPY --from=web /web/dist ./src/jarvis_core/web

RUN useradd --create-home --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
CMD ["uvicorn", "--factory", "jarvis_core.api:create_app", "--host", "0.0.0.0", "--port", "8080"]
