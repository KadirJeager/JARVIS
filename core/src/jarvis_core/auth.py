"""Request authentication for the two kinds of callers.

- People: a Firebase Authentication ID token whose verified email is in the
  installation allowlist. The Firebase `uid` becomes the data scope.
- Cloud Tasks and Cloud Scheduler: a Google-signed OIDC token issued for this
  service's URL to the configured invoker service account, as in the old
  `require_scheduler` check.

Google's verifiers are synchronous (they may fetch signing certificates), so
they run in a worker thread instead of blocking the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

import anyio.to_thread
from fastapi import HTTPException, Request
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from jarvis_core.config import DeploymentConfig

_transport = google_requests.Request()


@dataclass(frozen=True)
class User:
    uid: str
    email: str


def _bearer(request: Request) -> str:
    header = request.headers.get('authorization', '')
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(status_code=401, detail='bearer token required')
    return token


async def require_user(request: Request, config: DeploymentConfig) -> User:
    token = _bearer(request)
    try:
        claims = await anyio.to_thread.run_sync(
            lambda: id_token.verify_firebase_token(token, _transport, audience=config.project_id)
        )
    except (ValueError, GoogleAuthError):
        raise HTTPException(status_code=401, detail='invalid token') from None
    email = str(claims.get('email', '')).lower()
    if not claims.get('email_verified') or email not in config.allowed_emails:
        raise HTTPException(status_code=403, detail='account not allowed')
    return User(uid=str(claims['sub']), email=email)


async def require_invoker(request: Request, config: DeploymentConfig) -> None:
    token = _bearer(request)
    try:
        claims = await anyio.to_thread.run_sync(
            lambda: id_token.verify_oauth2_token(token, _transport, audience=config.service_url)
        )
    except (ValueError, GoogleAuthError):
        raise HTTPException(status_code=401, detail='invalid token') from None
    if not claims.get('email_verified') or claims.get('email') != config.invoker_service_account:
        raise HTTPException(status_code=403, detail='caller not allowed')
