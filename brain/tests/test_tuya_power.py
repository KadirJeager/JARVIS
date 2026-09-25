"""Tuya Cloud contract tests. All requests use a synthetic HTTP transport."""

import hashlib
import hmac
import json

import httpx
import pytest

from app.tuya_power import TuyaCloudClient, TuyaError


DEVICE = "pc-card-test"
ENDPOINT = "https://openapi.tuyaeu.com"
ACCESS_ID = "test-id"
SECRET = "test-secret"


def _response(result, *, success=True):
    return httpx.Response(200, json={"success": success, "result": result})


def _token(value="access-1", refresh="refresh-1", expiry=7200):
    return _response({"access_token": value, "refresh_token": refresh,
                      "expire_time": expiry})


def _client(handler, *, clock=None):
    now = clock or [1_000.0]
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return TuyaCloudClient(endpoint=ENDPOINT, access_id=ACCESS_ID,
                           access_secret=SECRET, http=http,
                           now=lambda: now[0], nonce=lambda: "nonce-test")


def _signed(request, *, token=None):
    assert request.headers["client_id"] == ACCESS_ID
    assert request.headers["sign_method"] == "HMAC-SHA256"
    assert request.headers["t"] == "1000000"
    assert request.headers["nonce"] == "nonce-test"
    if token is None:
        assert "access_token" not in request.headers
    else:
        assert request.headers["access_token"] == token
    body_hash = hashlib.sha256(request.content).hexdigest()
    path = request.url.raw_path.decode("ascii")
    to_sign = f"{request.method}\n{body_hash}\n\n{path}"
    source = f"{ACCESS_ID}{token or ''}1000000nonce-test{to_sign}"
    expected = hmac.new(SECRET.encode(), source.encode(), hashlib.sha256).hexdigest().upper()
    assert request.headers["sign"] == expected


def test_signed_discovery_reads_device_functions_specification_and_status():
    paths = []

    def handle(request):
        path = request.url.path
        paths.append(path)
        if path == "/v1.0/token":
            assert request.url.query == b"grant_type=1"
            _signed(request)
            return _token()
        _signed(request, token="access-1")
        if path.endswith("/functions"):
            return _response({"functions": [{"code": "power_pulse", "type": "Boolean"}]})
        if path.endswith("/specification"):
            return _response({"category": "kg", "functions": [], "status": []})
        if path.endswith("/status"):
            return _response([{"code": "power_pulse", "value": False}])
        return _response({"id": DEVICE, "online": True})

    client = _client(handle)
    assert client.get_device(DEVICE)["online"] is True
    assert client.get_functions(DEVICE)[0]["code"] == "power_pulse"
    assert client.get_specifications(DEVICE)["category"] == "kg"
    assert client.get_status(DEVICE)[0]["value"] is False
    assert paths == ["/v1.0/token", f"/v1.0/iot-03/devices/{DEVICE}",
                     f"/v1.0/iot-03/devices/{DEVICE}/functions",
                     f"/v1.0/iot-03/devices/{DEVICE}/specification",
                     f"/v1.0/iot-03/devices/{DEVICE}/status"]


def test_expired_token_reauthenticates_once_and_uses_new_token():
    now = [1_000.0]
    paths = []
    grants = 0

    def handle(request):
        nonlocal grants
        paths.append(request.url.path)
        if request.url.path == "/v1.0/token":
            grants += 1
            assert request.url.query == b"grant_type=1"
            return _token(f"access-{grants}", f"refresh-{grants}", expiry=120)
        assert request.headers["access_token"] == ("access-1" if now[0] == 1_000 else "access-2")
        return _response([{"code": "relay", "value": False}])

    client = _client(handle, clock=now)
    client.get_status(DEVICE)
    now[0] += 120
    client.get_status(DEVICE)
    assert paths == ["/v1.0/token", f"/v1.0/iot-03/devices/{DEVICE}/status",
                     "/v1.0/token", f"/v1.0/iot-03/devices/{DEVICE}/status"]


def test_single_explicit_command_checks_live_function_and_never_retries_post():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/v1.0/token":
            return _token()
        if request.url.path.endswith("/functions"):
            return _response({"functions": [{"code": "relay_button", "type": "Boolean"}]})
        if request.method == "POST":
            _signed(request, token="access-1")
            assert json.loads(request.content) == {
                "commands": [{"code": "relay_button", "value": True}]
            }
            return _response(True)
        raise AssertionError("Unexpected request")

    client = _client(handle)
    with pytest.raises(ValueError, match="absent"):
        client.send_command(device_id=DEVICE, code="switch_1", value=True)
    with pytest.raises(ValueError, match="Boolean"):
        client.send_command(device_id=DEVICE, code="relay_button", value="true")
    client.send_command(device_id=DEVICE, code="relay_button", value=True)
    assert sum(request.method == "POST" for request in requests) == 1


def test_ambiguous_post_timeout_is_redacted_and_not_retried():
    posts = []

    def handle(request):
        if request.url.path == "/v1.0/token":
            return _token()
        if request.url.path.endswith("/functions"):
            return _response({"functions": [{"code": "relay_button", "type": "Boolean"}]})
        posts.append(request)
        raise httpx.ReadTimeout("test-secret refresh-1", request=request)

    client = _client(handle)
    with pytest.raises(TuyaError) as raised:
        client.send_command(device_id=DEVICE, code="relay_button", value=True)
    assert len(posts) == 1
    assert "test-secret" not in str(raised.value)
    assert "refresh-1" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_post_rejection_does_not_retry_even_for_token_expiry():
    posts = []

    def handle(request):
        if request.url.path == "/v1.0/token":
            return _token()
        if request.url.path.endswith("/functions"):
            return _response({"functions": [{"code": "relay_button", "type": "Boolean"}]})
        posts.append(request)
        return httpx.Response(200, json={"success": False, "code": 1010,
                                         "msg": "test-secret should stay private"})

    client = _client(handle)
    with pytest.raises(TuyaError) as raised:
        client.send_command(device_id=DEVICE, code="relay_button", value=True)
    assert len(posts) == 1
    assert "test-secret" not in str(raised.value)


def test_failed_command_result_and_invalid_inputs_fail_closed():
    posts = []

    def handle(request):
        if request.url.path == "/v1.0/token":
            return _token()
        if request.url.path.endswith("/functions"):
            return _response({"functions": [{"code": "relay_button", "type": "Boolean"}]})
        posts.append(request)
        return _response(False)

    client = _client(handle)
    with pytest.raises(TuyaError, match="did not accept"):
        client.send_command(device_id=DEVICE, code="relay_button", value=True)
    with pytest.raises(ValueError, match="device ID"):
        client.get_functions("../bad")
    with pytest.raises(ValueError, match="code and value"):
        client.send_command(device_id=DEVICE, code="relay_button", value=None)
    assert len(posts) == 1
    with pytest.raises(ValueError, match="official Tuya"):
        TuyaCloudClient(endpoint="https://other.example", access_id=ACCESS_ID,
                        access_secret=SECRET)
