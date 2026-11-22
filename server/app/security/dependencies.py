"""Authentication and tenant authorization dependencies.

Owns get_current_user, require_role, company and mine scoping, and the force-password gate.
Must NOT handle HTTP response cookie setting directly or manage rate limit buckets.
"""
import logging
from typing import Callable, List, Optional
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.mine import Mine
from app.models.user import User, UserMine
from app.security.roles import ROLE_MANAGER, SUPERIOR_ROLES, role_group
from app.security.tokens import (
    COOKIE_NAME,
    decode_session_token,
    decode_session_token_payload,
)

security_logger = logging.getLogger("security")
security_logger.setLevel(logging.INFO)


class PasswordChangeRequiredException(Exception):
    """Raised when a user must change their password before accessing resources."""
    pass


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticates the incoming request using the HttpOnly session cookie.

    Enforces that the user is active on every request and intercepts accounts
    flagged with force_pw_change=True (except for /me, /logout, /change-password).
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: session cookie missing",
        )

    payload = decode_session_token_payload(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: invalid or expired session",
        )

    try:
        user_id = int(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail="Authentication required: invalid or expired session",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.active:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: account is inactive or not found",
        )

    # Session invalidation (A3): reject if token tv != user's token_version
    # Tokens issued without tv claim are treated as tv=0
    token_tv = int(payload.get("tv", 0))
    user_tv = int(user.token_version)
    if token_tv != user_tv:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: session has been invalidated",
        )

    # Force password change gate
    exempted_paths = {
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/change-password",
    }
    if user.force_pw_change and request.url.path not in exempted_paths:
        raise PasswordChangeRequiredException()

    return user


def require_role(*roles: str) -> Callable:
    """Enforces that the authenticated user possesses one of the authorized roles."""
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: role '{user.role}' is not authorized for this action",
            )
        return user
    return dependency


def get_allowed_mine_ids(db: Session, user: User) -> List[int]:
    """Returns the list of mine IDs accessible to the user, strictly company-scoped.

    Manager: user_mines records if assigned, otherwise all mines in the company.
    All other roles: strict user_mines assignment only.
    """
    if user.role == ROLE_MANAGER:
        assigned = (
            db.query(UserMine.mine_id)
            .join(Mine, Mine.id == UserMine.mine_id)
            .filter(UserMine.user_id == user.id, Mine.company_id == user.company_id)
            .all()
        )
        if assigned:
            return [m[0] for m in assigned]
        all_company_mines = (
            db.query(Mine.id)
            .filter(Mine.company_id == user.company_id)
            .all()
        )
        return [m[0] for m in all_company_mines]

    assigned = (
        db.query(UserMine.mine_id)
        .join(Mine, Mine.id == UserMine.mine_id)
        .filter(UserMine.user_id == user.id, Mine.company_id == user.company_id)
        .all()
    )
    return [m[0] for m in assigned]


def get_visible_tunnel_ids(db: Session, user: User) -> Optional[List[int]]:
    """Returns accessible tunnel IDs within permitted mines.

    TODO(step 5): The foreman/miner 'own tunnel' rule requires the assignments table
    from Step 5. Until assignments exist, workers can see all tunnels in their permitted mines.
    """
    return None
