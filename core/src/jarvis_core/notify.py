"""Standard Web Push (VAPID) notifications to the owner's installed PWAs.

Browsers subscribe through the Push API and the PWA registers the
subscription here; a subscription belongs to one device and one user. When a
turn settles with a reply or an error, every subscription of the owner gets a
small encrypted payload with the conversation id, its title and the start of
the reply. The payload is data, not display text: the PWA's service worker
words the notification in the owner's language and skips it when the
conversation is already on screen.

The VAPID private key is a PEM EC P-256 key held as a secret; the deployment
creates it once. Without a key the notifier is not built and the panel
reports push as not configured. Push services answer 404 or 410 for a
subscription that no longer exists, which deletes it here.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.cloud.firestore import AsyncClient
from py_vapid import Vapid
from pydantic import BaseModel, Field, field_validator
from pywebpush import WebPushException, webpush_async

from jarvis_core.secrets import SecretResolver

_logger = logging.getLogger(__name__)

# A message waits this long on the push service for an offline device.
_TTL_SECONDS = 24 * 60 * 60
_SNIPPET_CHARS = 280
_GONE = frozenset({404, 410})
_MARKDOWN_NOISE = re.compile(r'[`*_#>~|]+|\[([^\]]*)\]\([^)]*\)')


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=256)


class PushSubscriptionIn(BaseModel):
    """`PushSubscription.toJSON()` from the browser plus a label the owner recognizes."""

    endpoint: str = Field(max_length=2048)
    keys: SubscriptionKeys
    label: str = Field(default='', max_length=120)

    @field_validator('endpoint')
    @classmethod
    def _push_service_url(cls, value: str) -> str:
        """The service posts to this URL, so only public HTTPS hosts are accepted."""
        parts = urlsplit(value)
        host = parts.hostname or ''
        if parts.scheme != 'https' or not host or host == 'localhost' or parts.username or parts.password:
            raise ValueError('endpoint must be a public https URL')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return value
        raise ValueError('endpoint must name a host, not an IP address')


def subscription_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode('utf-8')).hexdigest()[:40]


def snippet(text: str) -> str:
    """The start of a reply as plain text: markdown marks removed, whitespace collapsed."""
    plain = ' '.join(_MARKDOWN_NOISE.sub(lambda match: match.group(1) or ' ', text).split())
    return plain if len(plain) <= _SNIPPET_CHARS else plain[: _SNIPPET_CHARS - 1] + '…'


def _load_key(pem: str) -> Vapid:
    key = serialization.load_pem_private_key(pem.encode('utf-8'), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('VAPID key must be an EC P-256 private key')
    vapid = Vapid()
    vapid.private_key = key
    return vapid


def _public_key(vapid: Vapid) -> str:
    """The browser's `applicationServerKey`: the uncompressed point, base64url without padding."""
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class PushNotifier:
    """Stores subscriptions under `users/{uid}/push_subscriptions` and sends Web Push messages."""

    def __init__(self, client: AsyncClient, secrets: SecretResolver, *, key_ref: str, subject: str) -> None:
        self._users = client.collection('users')
        self._secrets = secrets
        self._key_ref = key_ref
        self._subject = subject
        self._vapid: Vapid | None = None

    async def _key(self) -> Vapid:
        if self._vapid is None:
            pem = await self._secrets.resolve(self._key_ref)
            if pem is None:
                raise ValueError('VAPID key reference resolved to nothing')
            self._vapid = _load_key(pem)
        return self._vapid

    def _subscriptions(self, uid: str):  # noqa: ANN202 - Firestore reference type
        return self._users.document(uid).collection('push_subscriptions')

    async def status(self, uid: str) -> dict[str, Any]:
        """Public key and the owner's subscriptions; endpoints and keys stay on the server."""
        subscriptions = []
        async for snapshot in self._subscriptions(uid).stream():
            record = snapshot.to_dict() or {}
            visible = {name: value for name, value in record.items() if name not in ('keys', 'endpoint')}
            subscriptions.append({'id': snapshot.id, **visible})
        return {'configured': True, 'public_key': _public_key(await self._key()), 'subscriptions': subscriptions}

    async def subscribe(self, uid: str, subscription: PushSubscriptionIn, *, user_agent: str) -> str:
        key = subscription_id(subscription.endpoint)
        reference = self._subscriptions(uid).document(key)
        existing = await reference.get()
        now = datetime.now(timezone.utc)
        await reference.set({
            'endpoint': subscription.endpoint,
            'keys': subscription.keys.model_dump(),
            'label': subscription.label.strip() or user_agent[:120],
            'created_at': (existing.to_dict() or {}).get('created_at', now) if existing.exists else now,
            'updated_at': now,
        }, merge=True)
        return key

    async def unsubscribe(self, uid: str, key: str) -> bool:
        reference = self._subscriptions(uid).document(key)
        if not (await reference.get()).exists:
            return False
        await reference.delete()
        return True

    async def send(self, uid: str, payload: dict[str, Any], *, only: str | None = None) -> list[dict[str, Any]]:
        """Send `payload` to the owner's subscriptions (or the one named `only`); report each outcome."""
        vapid = await self._key()
        data = json.dumps(payload, ensure_ascii=False)
        outcomes: list[dict[str, Any]] = []
        async for snapshot in self._subscriptions(uid).stream():
            if only is not None and snapshot.id != only:
                continue
            record = snapshot.to_dict() or {}
            now = datetime.now(timezone.utc)
            try:
                await webpush_async(
                    subscription_info={'endpoint': record['endpoint'], 'keys': record['keys']},
                    data=data,
                    vapid_private_key=vapid,
                    vapid_claims={'sub': self._subject},
                    ttl=_TTL_SECONDS,
                    timeout=10,
                )
            except WebPushException as exc:
                status = exc.status_code
                if status in _GONE:
                    await snapshot.reference.delete()
                    outcomes.append({'id': snapshot.id, 'ok': False, 'status': status, 'removed': True})
                    continue
                await snapshot.reference.update({'last_failure': {'status': status, 'at': now}})
                outcomes.append({'id': snapshot.id, 'ok': False, 'status': status, 'removed': False})
                continue
            except Exception as exc:  # noqa: BLE001 - one unreachable push service must not stop the others
                failure = {'status': None, 'error': type(exc).__name__, 'at': now}
                await snapshot.reference.update({'last_failure': failure})
                outcomes.append({'id': snapshot.id, 'ok': False, 'status': None, 'error': type(exc).__name__})
                continue
            await snapshot.reference.update({'last_success_at': now})
            outcomes.append({'id': snapshot.id, 'ok': True})
        return outcomes

    async def turn_settled(self, turn: dict[str, Any], *, kind: Literal['reply', 'error'], text: str) -> None:
        """Tell the owner's devices; failures are logged, never raised into the turn."""
        try:
            conversation = await (
                self._users.document(turn['uid']).collection('conversations').document(turn['conversation_id']).get()
            )
            payload = {
                'kind': kind,
                'conversation_id': turn['conversation_id'],
                'turn_id': turn['turn_id'],
                'title': (conversation.to_dict() or {}).get('title', ''),
                'text': snippet(text) if kind == 'reply' else text[:_SNIPPET_CHARS],
            }
            await self.send(turn['uid'], payload)
        except Exception:  # noqa: BLE001 - notification is best effort; the turn is already settled
            _logger.warning('push notification failed for turn %s', turn.get('turn_id'), exc_info=True)
