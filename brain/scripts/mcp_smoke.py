#!/usr/bin/env python3
"""Live smoke test for the Misafir Kapısı MCP endpoint (brain/app/guest_gate.py).

Runs a real MCP streamable-HTTP session against a DEPLOYED jarvis-brain:
initialize -> tools/list (exact expected green-zone set) -> tools/call
(get_user_profile). Auth: Google ID token (JARVIS_SMOKE_TOKEN env, minted by
the tablet/script with the jarvis OAuth client id).

Usage: JARVIS_SMOKE_TOKEN=... python scripts/mcp_smoke.py [base_url]
"""
import json
import os
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://jarvis-brain-xxxxxxxxxx-ew.a.run.app"
MCP_PATH = "/mcp/"  # trailing slash: /mcp 307-redirects to /mcp/ and urllib won't re-POST
TOKEN = os.environ["JARVIS_SMOKE_TOKEN"]
EXPECTED_TOOLS = {
    "get_user_profile", "search_memory", "remember_fact",
    "add_lesson", "list_watched_repos", "get_repo_updates",
}


def post(path, payload, session=None):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
        # Streamable HTTP may answer SSE; take the JSON-RPC line from the event stream.
        if body.startswith("event:") or "\ndata:" in body or body.startswith("data:"):
            for line in body.splitlines():
                if line.startswith("data:"):
                    body = line[5:].strip()
        return json.loads(body), resp.headers.get("mcp-session-id")


def main():
    # 1) unauthenticated must be 401
    try:
        req = urllib.request.Request(
            BASE + MCP_PATH, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=15)
        print("FAIL: unauthenticated request was not rejected")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"expected 401, got {e.code}"
        print("ok: unauthenticated -> 401")

    # 2) initialize
    resp, session = post(MCP_PATH, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "mcp-smoke", "version": "0.1"}},
    })
    assert resp.get("result", {}).get("serverInfo"), f"initialize failed: {resp}"
    print(f"ok: initialize -> {resp['result']['serverInfo']}")

    # 3) tools/list
    resp, _ = post(MCP_PATH, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session)
    tools = {t["name"] for t in resp["result"]["tools"]}
    assert tools == EXPECTED_TOOLS, f"tool set mismatch: {tools} != {EXPECTED_TOOLS}"
    print(f"ok: tools/list -> {sorted(tools)}")

    # 4) tools/call get_user_profile
    resp, _ = post(MCP_PATH, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_user_profile", "arguments": {}},
    }, session)
    content = resp["result"]["content"]
    assert content, f"empty tool result: {resp}"
    print(f"ok: tools/call get_user_profile -> {str(content)[:120]}")
    print("PASS: guest gate live smoke")


if __name__ == "__main__":
    main()
