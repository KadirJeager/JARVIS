"""Misafir Kapısı (app/guest_gate.py, North Star §4.9) uçtan uca testleri.

Desen test_api.py'nin aynısı: TestClient(main_mod.app) + id_token
monkeypatch. Fark: auth burada FastAPI dependency'si değil ASGI middleware
(guest_gate._GuestAuth), o yüzden dependency_overrides YERİNE her istekte
Bearer header'ı gidiyor. Lazy init gerçek Firestore'a gitmesin diye
main._init no-op'a, main._audit FakeAudit'e, tools._memory FakeDB'li
Memory'ye bağlanıyor."""
import json

import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.config as config_mod
import app.main as main_mod
from app import repo_watch, tools
from app.memory import Memory
from tests.fakes import FakeDB

EMAIL = "owner@example.com"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
# §4.9 başlangıç seti: green ∩ ADK-bağımsız. get_speaker_status green ama
# tool_context istediği için YOK; sarı yazma araçları bu fazda YOK.
EXPECTED_TOOLS = {
    "get_user_profile",
    "search_memory",
    "remember_fact",
    "add_lesson",
    "list_watched_repos",
    "get_repo_updates",
}


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def _auth(token="faketoken"):
    return {**MCP_HEADERS, "Authorization": f"Bearer {token}"}


def _rpc(method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _call(client, name, arguments=None, req_id=1):
    return client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": name, "arguments": arguments or {}}, req_id),
        headers=_auth(),
    )


def _result_payload(response):
    """tools/call cevabındaki araç çıktısı (mcp 1.28.1 şekilleri): dict dönüş
    yalnızca text content'te pretty JSON; list dönüşte structuredContent
    {"result": [...]} sarmalı (tek elemanlıda text yalnız ilk eleman, o
    yüzden structuredContent tercih edilir); str dönüş düz metin."""
    result = response.json()["result"]
    assert result["isError"] is False, result
    structured = result.get("structuredContent")
    if structured is not None:
        return structured["result"] if set(structured) == {"result"} else structured
    text = result["content"][0]["text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _good_token(token, request, client_id):
    return {"email": EMAIL, "email_verified": True}


@pytest.fixture()
def db():
    return FakeDB()


@pytest.fixture()
def gate_client(monkeypatch, db):
    """Auth'u geçen, fake memory + fake audit'li hazır client."""
    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", _good_token)
    # Gerçek _init Firestore client açar; testte no-op. Araçlar tools._memory
    # üzerinden FakeDB'ye, audit main._audit üzerinden FakeAudit'e gider.
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    audit = FakeAudit()
    monkeypatch.setattr(main_mod, "_audit", audit)
    monkeypatch.setattr(tools, "_memory", Memory(db))
    with TestClient(main_mod.app) as c:
        yield c, audit, db


# -- handshake + araç listesi -------------------------------------------------


def test_initialize_handshake(gate_client):
    client, _, _ = gate_client
    r = client.post(
        "/mcp",
        json=_rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        }),
        headers=_auth(),
    )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "jarvis-guest-gate"
    assert "tools" in result["capabilities"]


def test_tools_list_is_exact_guest_set(gate_client):
    client, _, _ = gate_client
    r = client.post("/mcp", json=_rpc("tools/list", {}), headers=_auth())
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == EXPECTED_TOOLS
    # Green ama ADK tool_context'li → kapıda olmamalı:
    assert "get_speaker_status" not in names
    # Sarı yazma araçları bu fazda kapalı:
    assert not {"update_user_profile", "watch_repo", "unwatch_repo"} & names


# -- tools/call happy path -----------------------------------------------------


def test_call_get_user_profile(gate_client):
    client, _, db = gate_client
    db.collection("profile").document("main").set({"coffee": "filtre"})
    payload = _result_payload(_call(client, "get_user_profile"))
    assert payload == {"coffee": "filtre"}


def test_call_remember_fact_writes_memory(gate_client):
    client, _, db = gate_client
    payload = _result_payload(_call(client, "remember_fact", {"fact": "Kadir çay sever"}))
    assert payload == "kaydedildi"
    stored = list(db.collection("facts").docs.values())
    assert len(stored) == 1 and stored[0]["text"] == "Kadir çay sever"


def test_call_add_lesson_writes_memory(gate_client):
    client, _, db = gate_client
    payload = _result_payload(_call(client, "add_lesson", {
        "context": "deploy", "tried": "X", "went_wrong": "Y", "correct": "Z",
    }))
    assert payload == "ders kaydedildi"
    stored = list(db.collection("lessons").docs.values())
    assert len(stored) == 1 and stored[0]["correct"] == "Z"


def test_call_search_memory(gate_client):
    client, _, _ = gate_client
    tools.remember_fact("Kadir filtre kahve içer")
    payload = _result_payload(_call(client, "search_memory", {"query": "kahve"}))
    assert payload and "kahve" in payload[0]["text"]


def test_call_list_watched_repos(gate_client):
    client, _, db = gate_client
    db.collection(repo_watch.WATCH_COLLECTION).document("a_b").set(
        {"repo": "a/b", "note": "n", "last_check": None, "last_error": None}
    )
    payload = _result_payload(_call(client, "list_watched_repos"))
    assert payload["sayi"] == 1
    assert payload["repolar"][0]["repo"] == "a/b"


def test_call_get_repo_updates_marks_surfaced(gate_client):
    client, _, db = gate_client
    db.collection(repo_watch.EVENTS_COLLECTION).document("e1").set({
        "repo": "a/b", "kind": "release", "title": "v1", "detail": "d",
        "url": "u", "ts": "2026-01-01", "surfaced": False,
    })
    payload = _result_payload(_call(client, "get_repo_updates"))
    assert payload["sayi"] == 1
    assert payload["olaylar"][0]["title"] == "v1"
    # Araç, döndürdüğü olayı surfaced=True olarak işaretler (tools.py davranışı).
    assert db.collection(repo_watch.EVENTS_COLLECTION).docs["e1"]["surfaced"] is True


# -- auth -----------------------------------------------------------------------


def test_no_token_returns_401():
    with TestClient(main_mod.app) as c:
        r = c.post("/mcp", json=_rpc("tools/list", {}), headers=MCP_HEADERS)
    assert r.status_code == 401


def test_bogus_token_returns_401(monkeypatch):
    def fake_verify(token, request, client_id):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        r = c.post("/mcp", json=_rpc("tools/list", {}), headers=_auth("bogus"))
    assert r.status_code == 401


def test_non_allowlisted_email_returns_403(monkeypatch):
    def fake_verify(token, request, client_id):
        return {"email": "attacker@evil.com", "email_verified": True}

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        r = c.post("/mcp", json=_rpc("tools/list", {}), headers=_auth("sometoken"))
    assert r.status_code == 403


def test_allowlisted_unverified_email_returns_403(monkeypatch):
    def fake_verify(token, request, client_id):
        return {"email": EMAIL, "email_verified": False}

    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    with TestClient(main_mod.app) as c:
        r = c.post("/mcp", json=_rpc("tools/list", {}), headers=_auth("sometoken"))
    assert r.status_code == 403


# -- policy + audit ---------------------------------------------------------------


def test_call_writes_guest_audit_entry(gate_client):
    client, audit, _ = gate_client
    long_fact = "x" * 1000
    _result_payload(_call(client, "remember_fact", {"fact": long_fact}))
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry["actor"] == f"guest:{EMAIL}"
    assert entry["tool"] == "remember_fact"
    assert entry["zone"] == "green"
    assert entry["decision"] == "allow"
    # 500 char kesim (policy.write_audit sözleşmesi):
    assert len(entry["args"]["fact"]) == 500
    assert entry["ts"]


def test_non_green_zone_blocked_with_audit(gate_client, monkeypatch):
    """Derinlikli savunma: config ileride bir aracı green'den indirirse kapı,
    araç kayıtlı olmasına rağmen çağrıyı reddeder ve block audit'i yazar."""
    client, audit, db = gate_client
    monkeypatch.setitem(config_mod.TOOL_ZONES, "remember_fact", config_mod.ZONE_YELLOW)
    r = _call(client, "remember_fact", {"fact": "sızmalı"})
    result = r.json()["result"]
    assert result["isError"] is True
    assert "POLİTİKA ENGELİ" in result["content"][0]["text"]
    # Araç hiç çalışmadı:
    assert db.collection("facts").docs == {}
    assert audit.entries[0]["decision"] == "block"
    assert audit.entries[0]["zone"] == "yellow"
    assert audit.entries[0]["actor"] == f"guest:{EMAIL}"


# -- JSON-RPC hata şekilleri ------------------------------------------------------


def test_unknown_method_returns_jsonrpc_error(gate_client):
    client, _, _ = gate_client
    r = client.post("/mcp", json=_rpc("bogus/method"), headers=_auth())
    body = r.json()
    assert "error" in body and body["error"]["code"] < 0


def test_malformed_json_returns_parse_error(gate_client):
    client, _, _ = gate_client
    r = client.post("/mcp", content=b"{not json", headers=_auth())
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == -32700


def test_unknown_tool_returns_error_result(gate_client):
    client, _, _ = gate_client
    r = _call(client, "delete_everything")
    result = r.json()["result"]
    assert result["isError"] is True
    assert "Unknown tool" in result["content"][0]["text"]
