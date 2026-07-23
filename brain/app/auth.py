"""Google Sign-In ID token verification + email allowlist (North Star §5 public gates)."""
from fastapi import Header, HTTPException
from google.auth.transport import requests as grequests
from google.oauth2 import id_token

from . import config


def require_user(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    token = authorization.removeprefix("Bearer ")
    try:
        info = id_token.verify_oauth2_token(token, grequests.Request(), config.OAUTH_CLIENT_ID)
    except ValueError:
        raise HTTPException(status_code=401, detail="Geçersiz oturum")
    email = info.get("email", "")
    if email not in config.ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail="Bu hesap yetkili değil")
    if info.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="Bu hesap yetkili değil")
    return email
