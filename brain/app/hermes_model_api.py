"""OpenAI chat-completions boundary for a Hermes model provider.

The existing /api/chat endpoint runs JARVIS's ADK tool loop. Hermes needs to
own its own tool loop, so this endpoint forwards the protocol to an explicitly
configured, local model backend without invoking the ADK agent. The intended
backend is the existing CLIProxyAPI sidecar on localhost. No proxy credential
is exposed to Hermes; the bearer token authenticates only this JARVIS boundary.
"""

import hmac
import json
import os
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter(prefix="/v1")
MODEL_ALIAS = "jarvis-gemini"
MAX_BODY_BYTES = 4 * 1024 * 1024
UPSTREAM_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


def _authorize(authorization: str | None) -> None:
    expected = os.environ.get("JARVIS_HERMES_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Hermes provider is not configured")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _upstream() -> tuple[str, str, str]:
    base_url = os.environ.get("JARVIS_HERMES_UPSTREAM_BASE_URL", "").rstrip("/")
    backend_key = os.environ.get("JARVIS_HERMES_UPSTREAM_KEY", "")
    backend_model = os.environ.get("JARVIS_HERMES_UPSTREAM_MODEL", "")
    if not base_url or not backend_model:
        raise HTTPException(status_code=503, detail="Hermes model backend is not configured")
    parsed = urlsplit(base_url)
    try:
        local = (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
                 and parsed.port is not None and parsed.username is None and parsed.password is None
                 and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        local = False
    if not local:
        raise HTTPException(status_code=503, detail="Hermes model backend must be local")
    return base_url, backend_key, backend_model


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, follow_redirects=False, trust_env=False)


@router.get("/models")
async def models(authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    _upstream()
    return {"object": "list", "data": [{"id": MODEL_ALIAS, "object": "model", "owned_by": "jarvis"}]}


@router.post("/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    base_url, backend_key, backend_model = _upstream()
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request too large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    if not isinstance(payload, dict) or payload.get("model") != MODEL_ALIAS:
        raise HTTPException(status_code=400, detail="Unsupported model")
    if not isinstance(payload.get("messages"), list):
        raise HTTPException(status_code=400, detail="messages must be a list")
    if not isinstance(payload.get("stream", False), bool):
        raise HTTPException(status_code=400, detail="stream must be a boolean")

    # Keep messages, tools, tool_choice and provider metadata intact.
    payload["model"] = backend_model
    headers = {"Content-Type": "application/json"}
    if backend_key:
        headers["Authorization"] = f"Bearer {backend_key}"
    url = f"{base_url}/v1/chat/completions"

    if payload.get("stream"):
        client = _client()
        try:
            upstream_request = client.build_request("POST", url, headers=headers, json=payload)
            response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            raise HTTPException(status_code=502, detail="Model upstream unavailable") from None
        if response.status_code >= 400:
            await response.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail="Model upstream failed")

        async def events() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    try:
        async with _client() as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Model upstream unavailable") from None
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Model upstream failed")
    try:
        answer = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Invalid model response") from None
    return JSONResponse(answer, headers={"Cache-Control": "no-store"})
