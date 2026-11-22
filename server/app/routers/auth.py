"""HTTP router for authentication, session lifecycle, and credential updates.

Owns /api/auth/* endpoints (login, logout, me, change-password).
Must NOT handle user administration or bypass security dependencies.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    security_logger,
)
from app.security.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.password_policy import validate_new_password
from app.security.rate_limit import (
    check_login_rate_limit,
    record_login_failure,
    record_login_success,
)
from app.security.roles import role_group
from app.security.tokens import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session_token,
    decode_session_token,
    set_session_cookie,
)
from app.services.audit import record_audit

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/api/auth/login")
def api_login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Authenticates username/password credentials and issues an HttpOnly session cookie."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    clean_username = req.username.strip()

    # Rate limiting: 5 failures per (username, IP) per 15 min returns 429
    retry_after = check_login_rate_limit(clean_username, client_ip)
    if retry_after is not None:
        security_logger.warning(
            "Login rate limit exceeded for user '%s' from IP '%s'", clean_username, client_ip
        )
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.query(User).filter(User.username == clean_username).first()

    # Constant-time dummy hash verification if user not found to prevent timing enumeration
    if not user:
        verify_password(
            req.password,
            "pbkdf2_sha256$600000$00000000000000000000000000000000$0000000000000000000000000000000000000000000000000000000000000000",
        )
        record_login_failure(clean_username, client_ip)
        security_logger.info("Failed login: unknown user '%s' from IP '%s'", clean_username, client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.active or not verify_password(req.password, user.password_hash):
        record_login_failure(clean_username, client_ip)
        security_logger.info(
            "Failed login: invalid credentials or inactive user '%s' from IP '%s'",
            clean_username,
            client_ip,
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Success: reset rate limit bucket
    record_login_success(clean_username, client_ip)

    # Legacy hash rehash to current standard
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(req.password)
        db.commit()

    # Issue JWT in HttpOnly cookie
    token = create_session_token(user.id, token_version=getattr(user, "token_version", 0))
    set_session_cookie(response, token)

    security_logger.info(
        "Successful login for user '%s' (id=%d, role='%s') from IP '%s'",
        user.username,
        user.id,
        user.role,
        client_ip,
    )

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "role_group": role_group(user.role),
            "company_id": user.company_id,
            "force_pw_change": user.force_pw_change,
        },
        "force_pw_change": user.force_pw_change,
    }


@router.get("/api/auth/me")
def api_me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns the authenticated user profile and permitted mine scopes."""
    allowed_mines = get_allowed_mine_ids(db, user)
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "role_group": role_group(user.role),
            "company_id": user.company_id,
            "force_pw_change": user.force_pw_change,
        },
        "allowed_mine_ids": allowed_mines,
    }


@router.post("/api/auth/logout")
def api_logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Terminates session and clears the HttpOnly cookie. Always returns 200."""
    raw_token = request.cookies.get(COOKIE_NAME)
    if raw_token:
        try:
            user_id = decode_session_token(raw_token)
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                security_logger.info("User '%s' logged out", user.username)
        except Exception:
            pass

    clear_session_cookie(response)
    return {"message": "Logged out"}


@router.post("/api/auth/change-password")
def api_change_password(
    req: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Changes password, satisfies force_pw_change gate, and reissues session token."""
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_pw = req.new_password
    if new_pw == req.current_password:
        raise HTTPException(status_code=400, detail="New password must be different from current password")

    try:
        validate_new_password(new_pw, user.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user.password_hash = hash_password(new_pw)
    user.force_pw_change = False
    if hasattr(user, "token_version"):
        user.token_version = (getattr(user, "token_version", 0) or 0) + 1
    record_audit(
        db,
        user,
        action="auth.password_change",
        entity="user",
        entity_id=user.id,
        detail={},
    )
    db.commit()

    # Reissue session cookie with new token_version so caller stays logged in
    new_token = create_session_token(user.id, token_version=getattr(user, "token_version", 0))
    set_session_cookie(response, new_token)

    security_logger.info("Password changed successfully for user '%s' (id=%d)", user.username, user.id)
    return {"message": "Password changed successfully"}
