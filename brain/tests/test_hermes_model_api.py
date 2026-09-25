"""The Hermes boundary must preserve tool turns and SSE, and fail closed."""

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import hermes_model_api


def _app(monkeypatch, handler):
    monkeypatch.setenv("JARVIS_HERMES_TOKEN", "jarvis-test-token")
    monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_BASE_URL", "http://localhost:8317")
    monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_KEY", "backend-test-key")
    monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_MODEL", "gemini-test-flash")
    monkeypatch.setattr(
        hermes_model_api,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5),
    )
    app = FastAPI()
    app.include_router(hermes_model_api.router)
    return TestClient(app)


def test_hermes_tool_turn_is_forwarded_without_running_jarvis_agent(monkeypatch):
    seen = {}

    def upstream(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "model": "gemini-test-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "call-1", "type": "function", "function": {
                            "name": "terminal", "arguments": '{"command":"pwd"}'}}]},
                         "finish_reason": "tool_calls"}],
        })

    with _app(monkeypatch, upstream) as client:
        request = {
            "model": "jarvis-gemini",
            "messages": [{"role": "user", "content": "pwd çalıştır"}],
            "tools": [{"type": "function", "function": {"name": "terminal",
                       "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}],
            "tool_choice": "auto",
        }
        response = client.post("/v1/chat/completions", json=request,
                               headers={"Authorization": "Bearer jarvis-test-token"})

    assert response.status_code == 200
    assert seen["url"] == "http://localhost:8317/v1/chat/completions"
    assert seen["auth"] == "Bearer backend-test-key"
    assert seen["body"]["model"] == "gemini-test-flash"
    assert seen["body"]["messages"] == request["messages"]
    assert seen["body"]["tools"] == request["tools"]
    assert seen["body"]["tool_choice"] == "auto"
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "terminal"


def test_hermes_stream_preserves_sse_and_rejects_bad_credentials(monkeypatch):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')

    with _app(monkeypatch, upstream) as client:
        denied = client.post("/v1/chat/completions", json={"model": "jarvis-gemini", "messages": []})
        assert denied.status_code == 401
        assert not calls
        with client.stream("POST", "/v1/chat/completions", json={
            "model": "jarvis-gemini", "messages": [{"role": "user", "content": "OK"}], "stream": True,
        }, headers={"Authorization": "Bearer jarvis-test-token"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert b"".join(response.iter_bytes()).endswith(b"data: [DONE]\n\n")
        assert len(calls) == 1


def test_hermes_provider_requires_local_sidecar_and_known_model(monkeypatch):
    with _app(monkeypatch, lambda request: httpx.Response(200, json={})) as client:
        headers = {"Authorization": "Bearer jarvis-test-token"}
        wrong_model = client.post("/v1/chat/completions", json={"model": "other", "messages": []}, headers=headers)
        assert wrong_model.status_code == 400
        monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_BASE_URL", "https://example.com")
        remote_proxy = client.post("/v1/chat/completions", json={"model": "jarvis-gemini", "messages": []}, headers=headers)
        assert remote_proxy.status_code == 503
        monkeypatch.setenv("JARVIS_HERMES_UPSTREAM_BASE_URL", "http://localhost:8317@remote.example")
        disguised_remote = client.post("/v1/chat/completions", json={"model": "jarvis-gemini", "messages": []}, headers=headers)
        assert disguised_remote.status_code == 503
