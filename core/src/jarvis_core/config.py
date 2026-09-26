"""Deployment configuration read from the environment.

Identity and addresses are installation choices, so nothing here has a
default that names a person, project or URL; a missing value stops startup
with a message naming the variable.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field

_FIREBASE_WEB_KEYS = ('apiKey', 'authDomain', 'projectId', 'appId')


class ConfigError(RuntimeError):
    """A required deployment variable is missing or malformed."""


def _required(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise ConfigError(f'environment variable {name} is required')
    return value


@dataclass(frozen=True)
class DeploymentConfig:
    project_id: str
    """Google Cloud project hosting Firestore, Firebase Authentication and Cloud Tasks."""
    allowed_emails: frozenset[str]
    """Verified Firebase account emails allowed to use this installation."""
    service_url: str
    """Public HTTPS origin of this service; Cloud Tasks and Scheduler call it and sign tokens for it."""
    tasks_queue: str
    """Cloud Tasks queue name: `projects/<p>/locations/<l>/queues/<q>`."""
    invoker_service_account: str
    """Service account whose OIDC tokens Cloud Tasks and Scheduler present to internal endpoints."""
    worker_id: str
    firebase_web_config: dict[str, str] = field(default_factory=dict)
    """Public Firebase web app config the PWA initializes with (`apiKey`, `authDomain`, `projectId`, `appId`)."""
    vapid_key_ref: str | None = None
    """Secret reference to the Web Push VAPID private key (PEM); unset turns push notifications off."""
    secret_prefix: str | None = None
    """Secret id prefix the service may create and rotate for keys the owner enters; unset disables that."""

    @classmethod
    def from_env(cls) -> DeploymentConfig:
        emails = frozenset(e.strip().lower() for e in _required('JARVIS_ALLOWED_EMAILS').split(',') if e.strip())
        if not emails:
            raise ConfigError('JARVIS_ALLOWED_EMAILS must list at least one email')
        service_url = _required('JARVIS_SERVICE_URL').rstrip('/')
        if not service_url.startswith('https://'):
            raise ConfigError('JARVIS_SERVICE_URL must be an https origin')
        try:
            web_config = json.loads(_required('JARVIS_FIREBASE_WEB_CONFIG'))
        except json.JSONDecodeError as exc:
            raise ConfigError('JARVIS_FIREBASE_WEB_CONFIG must be a JSON object') from exc
        if not isinstance(web_config, dict) or any(not web_config.get(key) for key in _FIREBASE_WEB_KEYS):
            raise ConfigError(f'JARVIS_FIREBASE_WEB_CONFIG must include {", ".join(_FIREBASE_WEB_KEYS)}')
        revision = os.environ.get('K_REVISION', 'local')
        return cls(
            firebase_web_config={key: str(web_config[key]) for key in _FIREBASE_WEB_KEYS},
            project_id=_required('JARVIS_PROJECT_ID'),
            allowed_emails=emails,
            service_url=service_url,
            tasks_queue=_required('JARVIS_TASKS_QUEUE'),
            invoker_service_account=_required('JARVIS_INVOKER_SERVICE_ACCOUNT'),
            worker_id=f'{revision}/{socket.gethostname()}/{os.getpid()}',
            vapid_key_ref=os.environ.get('JARVIS_VAPID_KEY_REF', '').strip() or None,
            secret_prefix=os.environ.get('JARVIS_SECRET_PREFIX', '').strip() or None,
        )
