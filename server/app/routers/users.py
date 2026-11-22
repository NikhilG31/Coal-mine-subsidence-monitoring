"""HTTP router for user administration and tenant account lifecycle.

Owns /api/users endpoints with Manager authorization and company scoping.
Must NOT handle session authentication, password verification, or cross-company actions.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.mine import Mine
from app.models.user import User, UserMine
from app.security.dependencies import (
    require_role,
    security_logger,
)
from app.security.passwords import hash_password
from app.security.password_policy import validate_new_password
from app.security.roles import ROLE_MANAGER
from app.services.audit import record_audit

router = APIRouter(tags=["users"])

CREATABLE_ROLES = {"safety_officer", "engineer", "foreman", "miner"}


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

    try:
        validate_new_password(req.initial_password, clean_username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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

    record_audit(
        db,
        user,
        action="user.create",
        entity="user",
        entity_id=new_user.id,
        detail={"username": new_user.username, "role": new_user.role, "mine_ids": mine_ids},
    )
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

    if target_user.id == user.id:
        if req.active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own Manager account")
        if req.role and req.role != ROLE_MANAGER:
            raise HTTPException(status_code=400, detail="Cannot demote your own Manager account")

    if target_user.role == ROLE_MANAGER and (req.active is False or (req.role and req.role != ROLE_MANAGER)):
        active_managers = (
            db.query(User)
            .filter(User.company_id == user.company_id, User.role == ROLE_MANAGER, User.active.is_(True))
            .count()
        )
        if active_managers <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote or deactivate the last active manager")

    changes = {}
    if req.role is not None:
        if req.role not in CREATABLE_ROLES and req.role != ROLE_MANAGER:
            raise HTTPException(status_code=403, detail="Invalid role assignment")
        if req.role == ROLE_MANAGER and target_user.role != ROLE_MANAGER:
            raise HTTPException(status_code=403, detail="Managers cannot promote accounts to Manager")
        target_user.role = req.role
        changes["role"] = req.role

    if req.full_name is not None:
        target_user.full_name = req.full_name.strip()
        changes["full_name"] = target_user.full_name

    if req.active is not None:
        target_user.active = req.active
        if req.active is False:
            target_user.token_version += 1
        changes["active"] = req.active

    if req.reset_password:
        try:
            validate_new_password(req.reset_password, target_user.username)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        target_user.password_hash = hash_password(req.reset_password)
        target_user.force_pw_change = True
        target_user.token_version += 1
        changes["reset_password"] = True

    if req.mine_ids is not None:
        if req.mine_ids:
            valid_count = (
                db.query(Mine.id)
                .filter(Mine.id.in_(req.mine_ids), Mine.company_id == user.company_id)
                .count()
            )
            if valid_count != len(set(req.mine_ids)):
                raise HTTPException(status_code=404, detail="One or more mines not found in company scope")
        db.query(UserMine).filter(UserMine.user_id == target_user.id).delete()
        for mid in set(req.mine_ids):
            db.add(UserMine(user_id=target_user.id, mine_id=mid))
        changes["mine_ids"] = list(set(req.mine_ids))

    record_audit(
        db,
        user,
        action="user.update",
        entity="user",
        entity_id=target_user.id,
        detail=changes,
    )
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
    target_user.token_version += 1
    record_audit(
        db,
        user,
        action="user.deactivate",
        entity="user",
        entity_id=target_user.id,
        detail={},
    )
    db.commit()
    security_logger.info("Manager '%s' deactivated user '%s' (id=%d)", user.username, target_user.username, target_user.id)
    return {"message": "User deactivated successfully"}
