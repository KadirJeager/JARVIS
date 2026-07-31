"""Google Workspace kimlik bilgileri yardımcısı (Secret Manager -> google.oauth2.credentials).

Faz Y2: Google Calendar ve Gmail API erişimi için Secret Manager'daki
`workspace-refresh-token` sırrını okur ve yenilenebilir Credentials nesnesi üretir.

Sır bulunamazsa veya yapılandırılmamışsa `WorkspaceNotConfigured` hatası fırlatılır;
poller'lar bu hatayı yakalayıp {handled: false, reason: "workspace yapılandırılmamış"}
döner ve sürecin çökmesini engeller.
"""
import json
import logging
import os
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

PROJECT_ID_ENV = "JARVIS_PROJECT_ID"
DEFAULT_PROJECT_ID = "your-gcp-project"
SECRET_NAME = "workspace-refresh-token"

_cached_credentials: Credentials | None = None
_cached_payload: dict[str, Any] | None = None


class WorkspaceNotConfigured(Exception):
    """Google Workspace entegrasyonu Secret Manager'da yapılandırılmamış veya eksik."""


def clear_credentials_cache() -> None:
    """Testler veya token yenileme sıfırlamaları için önbelleği temizler."""
    global _cached_credentials, _cached_payload
    _cached_credentials = None
    _cached_payload = None


def get_secret_payload(project_id: str | None = None) -> dict[str, Any]:
    """Secret Manager'dan workspace-refresh-token sırrını okur ve JSON dict döner."""
    global _cached_payload
    if _cached_payload is not None:
        return _cached_payload

    project = project_id or os.environ.get(PROJECT_ID_ENV, DEFAULT_PROJECT_ID)
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        payload_str = response.payload.data.decode("utf-8")
        payload = json.loads(payload_str)
        if not isinstance(payload, dict) or "refresh_token" not in payload:
            raise WorkspaceNotConfigured("Sır verisinde 'refresh_token' bulunamadı")
        _cached_payload = payload
        return payload
    except WorkspaceNotConfigured:
        raise
    except Exception as exc:
        logging.warning("workspace: Secret Manager erişim hatası (%s): %s", SECRET_NAME, exc)
        raise WorkspaceNotConfigured(f"Secret Manager erişilemedi: {exc}") from exc


def workspace_credentials(db=None) -> Credentials:
    """Secret Manager'dan alınan refresh_token ile erişim token'ı yenilenmiş Credentials döner.

    Önbellekli çalışır: Secret Manager'a tek okuma yapar. Token yoksa WorkspaceNotConfigured fırlatır.
    """
    global _cached_credentials
    if _cached_credentials is not None:
        if _cached_credentials.valid:
            return _cached_credentials
        if _cached_credentials.expired and _cached_credentials.refresh_token:
            try:
                _cached_credentials.refresh(Request())
                return _cached_credentials
            except Exception as exc:
                logging.warning("workspace: Token yenileme hatası: %s", exc)
                _cached_credentials = None

    payload = get_secret_payload()
    refresh_token = payload.get("refresh_token")
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")
    scopes = payload.get("scopes")

    if not refresh_token:
        raise WorkspaceNotConfigured("refresh_token eksik")

    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
        creds.refresh(Request())
        _cached_credentials = creds
        return creds
    except Exception as exc:
        logging.warning("workspace: Credentials oluşturma/yenileme hatası: %s", exc)
        raise WorkspaceNotConfigured(f"Token yenilenemedi: {exc}") from exc
