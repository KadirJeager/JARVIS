"""Small Tuya Cloud client for discovering and issuing one explicit device command.

This module does not decide when a PC should be powered on. In particular, it
never retries a command: a timed-out power-button pulse may already have run.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx


_TUYA_HOSTS = frozenset({
    "openapi.tuyacn.com",
    "openapi.tuyaus.com",
    "openapi-ueaz.tuyaus.com",
    "openapi.tuyaeu.com",
    "openapi-weaz.tuyaeu.com",
    "openapi.tuyain.com",
    "openapi-sg.iotbing.com",
})
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class TuyaError(RuntimeError):
    """A redacted Tuya request or response failure."""


class TuyaCloudClient:
    """Tuya OpenAPI client using cloud-project Access ID and Access Secret.

    Construct with the regional endpoint shown in the Tuya cloud project. The
    credentials and tokens remain in memory. ``send_command`` reads the device
    function set before its single command POST; it never guesses a DP code.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        access_id: str,
        access_secret: str,
        http: httpx.Client | None = None,
        now: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _TUYA_HOSTS
            or parsed.port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Use an official Tuya regional HTTPS endpoint")
        if not access_id or not access_secret:
            raise ValueError("Tuya cloud credentials are required")
        self._endpoint = endpoint.rstrip("/")
        self._access_id = access_id
        self._access_secret = access_secret
        self._http = http or httpx.Client(timeout=10.0)
        self._owns_http = http is None
        self._now = now
        self._nonce = nonce
        self._access_token: str | None = None
        self._token_until = 0.0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> TuyaCloudClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _device_path(device_id: str, suffix: str) -> str:
        if not _DEVICE_ID.fullmatch(device_id):
            raise ValueError("Invalid Tuya device ID")
        return f"/v1.0/iot-03/devices/{device_id}{suffix}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        body = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if payload is not None else b""
        )
        timestamp = str(int(self._now() * 1000))
        nonce = self._nonce()
        content_hash = hashlib.sha256(body).hexdigest()
        # Tuya's current cloud authorization signature includes the HTTP
        # method, body hash, optional signed headers (empty here), and URL.
        string_to_sign = f"{method}\n{content_hash}\n\n{path}"
        source = f"{self._access_id}{token or ''}{timestamp}{nonce}{string_to_sign}"
        signature = hmac.new(
            self._access_secret.encode("utf-8"), source.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()
        headers = {
            "client_id": self._access_id,
            "sign": signature,
            "sign_method": "HMAC-SHA256",
            "t": timestamp,
            "nonce": nonce,
        }
        if token is not None:
            headers["access_token"] = token
        if payload is not None:
            headers["content-type"] = "application/json"
        try:
            response = self._http.request(
                method, self._endpoint + path, headers=headers, content=body
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            # Transport messages can contain request headers. Never propagate
            # or chain those exceptions.
            raise TuyaError("Tuya request failed") from None
        if not isinstance(data, dict) or data.get("success") is not True:
            # Tuya's error message is remote-controlled and may echo input.
            raise TuyaError("Tuya rejected request")
        return data.get("result")

    def _store_token(self, result: Any) -> str:
        if not isinstance(result, dict):
            raise TuyaError("Tuya token response is invalid")
        access = result.get("access_token")
        expiry = result.get("expire_time")
        if (
            not isinstance(access, str) or not access
            or type(expiry) is not int or expiry <= 0
        ):
            raise TuyaError("Tuya token response is invalid")
        self._access_token = access
        self._token_until = self._now() + max(1, expiry - min(60, expiry // 10))
        return access

    def _token(self) -> str:
        if self._access_token and self._now() < self._token_until:
            return self._access_token
        # Cloud-project simple mode allows a fresh grant_type=1 token. Tuya's
        # refresh route embeds its single-use refresh token in the URL, which
        # HTTP client/proxy access logs could expose; avoid that route.
        result = self._request("GET", "/v1.0/token?grant_type=1")
        return self._store_token(result)

    def _get(self, path: str) -> Any:
        return self._request("GET", path, token=self._token())

    def get_device(self, device_id: str) -> dict[str, Any]:
        """Read basic details, including the Tuya device online flag."""
        result = self._get(self._device_path(device_id, ""))
        if not isinstance(result, dict):
            raise TuyaError("Tuya device response is invalid")
        return result

    def get_functions(self, device_id: str) -> list[dict[str, Any]]:
        """Read the device's actual command codes and value schemas."""
        result = self._get(self._device_path(device_id, "/functions"))
        functions = result.get("functions") if isinstance(result, dict) else None
        if not isinstance(functions, list) or not all(isinstance(f, dict) for f in functions):
            raise TuyaError("Tuya function response is invalid")
        return functions

    def get_specifications(self, device_id: str) -> dict[str, Any]:
        """Read the device's function and status specifications."""
        result = self._get(self._device_path(device_id, "/specification"))
        if not isinstance(result, dict):
            raise TuyaError("Tuya specification response is invalid")
        return result

    def get_status(self, device_id: str) -> list[dict[str, Any]]:
        """Read the last reported Tuya data point values."""
        result = self._get(self._device_path(device_id, "/status"))
        if not isinstance(result, list) or not all(isinstance(s, dict) for s in result):
            raise TuyaError("Tuya status response is invalid")
        return result

    def send_command(self, *, device_id: str, code: str, value: Any) -> None:
        """Send exactly one explicit command after checking the live function set.

        A ``None`` value is never a valid choice here. A successful response
        confirms API acceptance only; callers must verify the PC via its own
        heartbeat. Never retry this method after an ambiguous failure.
        """
        if not isinstance(code, str) or not code or value is None:
            raise ValueError("A command code and value are required")
        functions = self.get_functions(device_id)
        function = next((f for f in functions if f.get("code") == code), None)
        if function is None:
            raise ValueError("Command code is absent from device functions")
        expected_type = function.get("type")
        if expected_type == "Boolean" and type(value) is not bool:
            raise ValueError("Command value must be Boolean")
        if expected_type == "Integer" and type(value) is not int:
            raise ValueError("Command value must be Integer")
        if expected_type in ("Enum", "String") and not isinstance(value, str):
            raise ValueError("Command value must be a string")
        path = self._device_path(device_id, "/commands")
        result = self._request(
            "POST", path,
            payload={"commands": [{"code": code, "value": value}]},
            token=self._token(),
        )
        if result is not True:
            raise TuyaError("Tuya did not accept command")
