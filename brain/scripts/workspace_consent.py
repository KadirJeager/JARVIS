#!/usr/bin/env python3
"""One-time Google Workspace consent for JARVIS Sekreter (Faz Y2).

Runs the installed-app OAuth loopback flow with a DESKTOP OAuth client
(client_secret_*.json from Cloud Console > APIs & Services > Credentials),
requests Gmail + Calendar READONLY scopes, and stores the refresh token in
Secret Manager as `workspace-refresh-token` (your-gcp-project).

The Desktop client is required because the app's existing OAuth client is
Android-type and cannot drive an installed-app flow. Create it once in the
console (type: Desktop app), download the JSON, then:

    python scripts/workspace_consent.py path/to/client_secret_*.json

The token is what brain/app/workspace.py exchanges for Gmail/Calendar API
access tokens at runtime (users.googleapis.com is enabled project-wide).
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
PROJECT = "your-gcp-project"
SECRET_NAME = "workspace-refresh-token"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        sys.exit("no refresh token returned — rerun with prompt='consent'")
    payload = {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": SCOPES,
    }
    # Write via google-cloud-secret-manager (ADC = Kadir's admin account).
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT}/secrets/{SECRET_NAME}"
    try:
        client.create_secret(
            request={
                "parent": f"projects/{PROJECT}",
                "secret_id": SECRET_NAME,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    except Exception:
        pass  # already exists — add a version instead
    client.add_secret_version(
        request={"parent": parent, "payload": {"data": json.dumps(payload).encode()}}
    )
    print(f"refresh token stored in {parent} (scopes: {SCOPES})")


if __name__ == "__main__":
    main()
