"""HTTP router for authentication, session lifecycle, and user management.

Owns /api/auth/* endpoints and /api/users/* manager administration.
Must NOT bypass role dependencies or allow company tenant boundary crossing.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.mine import Mine
from app.models.user import User, UserMine
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
    security_logger,
)
from app.security.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.rate_limit import (
    check_login_rate_limit,
    record_login_failure,
    record_login_success,
)
from app.security.roles import (
    ROLE_MANAGER,
    role_group,
)
from app.security.tokens import (
    clear_session_cookie,
    create_session_token,
    set_session_cookie,
)

router = APIRouter(tags=["auth"])

CREATABLE_ROLES = {"safety_officer", "engineer", "foreman", "miner"}


# Request Schemas
class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    full_name: str
    role: str
    initial_password: str
    mine_ids: Optional[List[int]] = None


class PatchUserRequest(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    mine_ids: Optional[List[int]] = None
    reset_password: Optional[str] = None


# =============================================================================
# AUTHENTICATION ENDPOINTS
# =============================================================================

@router.post("/api/auth/login")
async def api_login(
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
    token = create_session_token(user.id)
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
def api_logout(response: Response, user: Optional[User] = Depends(get_current_user)):
    """Terminates session and clears the HttpOnly cookie."""
    clear_session_cookie(response)
    if user:
        security_logger.info("User '%s' logged out", user.username)
    return {"message": "Logged out"}


@router.post("/api/auth/change-password")
async def api_change_password(
    req: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Changes password, satisfies force_pw_change gate, and reissues session token."""
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_pw = req.new_password
    if len(new_pw) < 10:
        raise HTTPException(status_code=400, detail="New password must be at least 10 characters long")
    if new_pw == req.current_password:
        raise HTTPException(status_code=400, detail="New password must be different from current password")
    if new_pw.lower() == user.username.lower():
        raise HTTPException(status_code=400, detail="New password cannot match username")

    user.password_hash = hash_password(new_pw)
    user.force_pw_change = False
    db.commit()

    # Reissue session cookie
    new_token = create_session_token(user.id)
    set_session_cookie(response, new_token)

    security_logger.info("Password changed successfully for user '%s' (id=%d)", user.username, user.id)
    return {"message": "Password changed successfully"}


# =============================================================================
# USER MANAGEMENT (MANAGER ONLY)
# =============================================================================

@router.get("/api/users")
def api_users(
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Returns users belonging to the Manager's company."""
    company_users = (
        db.query(User)
        .filter(User.company_id == user.company_id)
        .order_by(User.id.asc())
        .all()
    )

    out = []
    for u in company_users:
        mine_ids = [um.mine_id for um in u.mine_associations]
        out.append(
            {
                "id": u.id,
                "username": u.username,
                "full_name": u.full_name,
                "role": u.role,
                "active": u.active,
                "force_pw_change": u.force_pw_change,
                "mine_ids": mine_ids,
            }
        )
    return {"users": out}


@router.post("/api/users")
def api_create_user(
    req: CreateUserRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Creates a new subordinate user within the Manager's company."""
    # Managers cannot create other managers
    if req.role not in CREATABLE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Managers cannot assign role '{req.role}'. Permitted roles: {sorted(CREATABLE_ROLES)}",
        )

    clean_username = req.username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    existing = db.query(User).filter(User.username == clean_username).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Username '{clean_username}' already exists")

    # Validate that every mine_id belongs to the Manager's company
    mine_ids = req.mine_ids or []
    if mine_ids:
        valid_count = (
            db.query(Mine.id)
            .filter(Mine.id.in_(mine_ids), Mine.company_id == user.company_id)
            .count()
        )
        if valid_count != len(set(mine_ids)):
            raise HTTPException(status_code=404, detail="One or more specified mines not found in company scope")

    new_user = User(
        company_id=user.company_id,
        username=clean_username,
        full_name=req.full_name.strip(),
        role=req.role,
        password_hash=hash_password(req.initial_password),
        active=True,
        force_pw_change=True,
    )
    db.add(new_user)
    db.flush()

    for mid in set(mine_ids):
        db.add(UserMine(user_id=new_user.id, mine_id=mid))

    db.commit()
    security_logger.info(
        "Manager '%s' created user '%s' (role='%s', id=%d)",
        user.username,
        new_user.username,
        new_user.role,
        new_user.id,
    )

    return {
        "id": new_user.id,
        "username": new_user.username,
        "full_name": new_user.full_name,
        "role": new_user.role,
        "active": new_user.active,
        "force_pw_change": new_user.force_pw_change,
        "mine_ids": mine_ids,
    }


@router.patch("/api/users/{user_id}")
def api_patch_user(
    user_id: int,
    req: PatchUserRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Updates user attributes, permitted mines, or triggers password reset."""
    target_user = (
        db.query(User)
        .filter(User.id == user_id, User.company_id == user.company_id)
        .first()
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Safety: Manager cannot demote or deactivate themselves
    if target_user.id == user.id:
        if req.active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own Manager account")
        if req.role and req.role != ROLE_MANAGER:
            raise HTTPException(status_code=400, detail="Cannot demote your own Manager account")

    # Safety: Cannot deactivate or demote the last active manager
    if target_user.role == ROLE_MANAGER and (req.active is False or (req.role and req.role != ROLE_MANAGER)):
        active_managers = (
            db.query(User)
            .filter(User.company_id == user.company_id, User.role == ROLE_MANAGER, User.active.is_(True))
            .count()
        )
        if active_managers <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote or deactivate the last active manager")

    # Role updates: cannot promote to manager
    if req.role is not None:
        if req.role not in CREATABLE_ROLES and req.role != ROLE_MANAGER:
            raise HTTPException(status_code=403, detail="Invalid role assignment")
        if req.role == ROLE_MANAGER and target_user.role != ROLE_MANAGER:
            raise HTTPException(status_code=403, detail="Managers cannot promote accounts to Manager")
        target_user.role = req.role

    if req.full_name is not None:
        target_user.full_name = req.full_name.strip()

    if req.active is not None:
        target_user.active = req.active

    if req.reset_password:
        target_user.password_hash = hash_password(req.reset_password)
        target_user.force_pw_change = True

    if req.mine_ids is not None:
        if req.mine_ids:
            valid_count = (
                db.query(Mine.id)
                .filter(Mine.id.in_(req.mine_ids), Mine.company_id == user.company_id)
                .count()
            )
            if valid_count != len(set(req.mine_ids)):
                raise HTTPException(status_code=404, detail="One or more mines not found in company scope")
        # Replace user_mines
        db.query(UserMine).filter(UserMine.user_id == target_user.id).delete()
        for mid in set(req.mine_ids):
            db.add(UserMine(user_id=target_user.id, mine_id=mid))

    db.commit()
    security_logger.info("Manager '%s' updated user '%s' (id=%d)", user.username, target_user.username, target_user.id)

    mine_ids = [um.mine_id for um in target_user.mine_associations]
    return {
        "id": target_user.id,
        "username": target_user.username,
        "full_name": target_user.full_name,
        "role": target_user.role,
        "active": target_user.active,
        "force_pw_change": target_user.force_pw_change,
        "mine_ids": mine_ids,
    }


@router.delete("/api/users/{user_id}")
def api_delete_user(
    user_id: int,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Soft deactivates a user (active=False). Never hard deletes."""
    target_user = (
        db.query(User)
        .filter(User.id == user_id, User.company_id == user.company_id)
        .first()
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if target_user.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own Manager account")

    if target_user.role == ROLE_MANAGER:
        active_managers = (
            db.query(User)
            .filter(User.company_id == user.company_id, User.role == ROLE_MANAGER, User.active.is_(True))
            .count()
        )
        if active_managers <= 1:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last active manager")

    target_user.active = False
    db.commit()
    security_logger.info("Manager '%s' deactivated user '%s' (id=%d)", user.username, target_user.username, target_user.id)
    return {"message": "User deactivated successfully"}
