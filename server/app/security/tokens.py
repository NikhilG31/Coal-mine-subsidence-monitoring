"""JWT session token creation and verification utilities.

Owns HS256 JWT encoding, expiration enforcement, and session cookie configuration.
Must NOT execute database lookups or load passwords.
"""
import datetime
import time
from typing import Optional
import jwt
from fastapi import Response

from app.config import (
    COOKIE_SECURE,
    JWT_SECRET,
    SESSION_HOURS,
    validate_jwt_secret,
)

COOKIE_NAME: str = "session"


def create_session_token(user_id: int, token_version: int = 0) -> str:
    """Creates a signed HS256 JWT session token with sub, tv, iat, and exp claims."""
    validate_jwt_secret()
    now_ts = int(time.time())
    exp_ts = now_ts + (SESSION_HOURS * 3600)
    payload = {
        "sub": str(user_id),
        "tv": int(token_version),
        "iat": now_ts,
        "exp": exp_ts,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_session_token_payload(token: str) -> Optional[dict]:
    """Decodes and validates a session JWT token. Returns payload dict if valid, else None."""
    validate_jwt_secret()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except (jwt.PyJWTError, ValueError):
        return None


def decode_session_token(token: str) -> Optional[int]:
    """Decodes and validates a session JWT token. Returns user_id if valid, else None."""
    payload = decode_session_token_payload(token)
    if not payload:
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        return int(sub)
    except (ValueError, TypeError):
        return None


def set_session_cookie(response: Response, token: str) -> None:
    """Sets the HttpOnly session cookie on the outgoing HTTP response."""
    max_age_s = SESSION_HOURS * 3600
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age_s,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Deletes the session cookie from the client."""
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )
