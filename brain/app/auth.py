"""Google Sign-In ID token verification + email allowlist (North Star §5 public gates)."""
from fastapi import Header, HTTPException
from google.auth.transport import requests as grequests
from google.oauth2 import id_token

from . import config


def verify_token_email(token: str) -> str:
    """Verify a Google ID token and return the allowlisted, verified email.

    Raises PermissionError (Turkish message) on invalid token, non-allowlisted
    email, or unverified email. Transport-agnostic: used by both the HTTP
    dependency (require_user) and the voice WebSocket handshake.
    """
    try:
        info = id_token.verify_oauth2_token(token, grequests.Request(), config.OAUTH_CLIENT_ID)
    except ValueError:
        raise PermissionError("Geçersiz oturum")
    email = info.get("email", "")
    if email not in config.ALLOWED_EMAILS:
        raise PermissionError("Bu hesap yetkili değil")
    if info.get("email_verified") is not True:
        raise PermissionError("Bu hesap yetkili değil")
    return email


def require_user(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    token = authorization.removeprefix("Bearer ")
    try:
        return verify_token_email(token)
    except PermissionError as exc:
        status_code = 401 if str(exc) == "Geçersiz oturum" else 403
        raise HTTPException(status_code=status_code, detail=str(exc))
