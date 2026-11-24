"""HTTP router for shift check-out exception flags and resolution.

Owns GET /api/check-flags and PATCH /api/check-flags/{id} endpoints.
Must NOT define database models, calculate raw sensor telemetry, or omit clearance audit logs.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from app.config import CHECK_FLAG_OVERDUE_HOURS
from app.database import get_db
from app.models.assignment import Assignment
from app.models.check_flag import CheckFlag
from app.models.tunnel import Tunnel
from app.models.user import User
from app.schemas.checkins import (
    CheckFlagsListResponse,
    ClearFlagRequest,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
)
from app.security.roles import (
    ROLE_ENGINEER,
    ROLE_FOREMAN,
    ROLE_MANAGER,
    ROLE_SAFETY_OFFICER,
)
from app.services import clock
from app.services.flag_clearing import FlagClearError, clear_flag
from app.services.tunnel_scope import get_current_tunnel_id

router = APIRouter(tags=["check-flags"])

SCANNER_ROLES = (ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN)


@router.get("/api/check-flags", response_model=CheckFlagsListResponse)
def api_list_check_flags(
    status_filter: str = Query(default="open", alias="status", pattern="^(open|all)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    mine_id: Optional[int] = None,
    tunnel_id: Optional[int] = None,
    user: User = Depends(require_role(*SCANNER_ROLES)),
    db: Session = Depends(get_db),
):
    """Lists shift check-out exception flags scoped to caller's permitted tunnels."""
    now = clock.utcnow()
    allowed_mines = get_allowed_mine_ids(db, user)

    if user.role == ROLE_FOREMAN:
        curr_tid = get_current_tunnel_id(db, user.id, now)
        if not curr_tid:
            return {"flags": []}
        if tunnel_id is not None and tunnel_id != curr_tid:
            raise HTTPException(status_code=403, detail="Foreman may only view own tunnel")
        target_tunnel_ids = [curr_tid]
    else:
        if tunnel_id is not None:
            tunnel = db.query(Tunnel).filter(Tunnel.id == tunnel_id).first()
            if not tunnel or tunnel.mine_id not in allowed_mines:
                raise HTTPException(status_code=404, detail="Tunnel not found")
            target_tunnel_ids = [tunnel_id]
        elif mine_id is not None:
            if mine_id not in allowed_mines:
                raise HTTPException(status_code=404, detail="Mine not found")
            tunnels = db.query(Tunnel).filter(Tunnel.mine_id == mine_id).all()
            target_tunnel_ids = [t.id for t in tunnels]
        else:
            tunnels = db.query(Tunnel).filter(Tunnel.mine_id.in_(allowed_mines)).all()
            target_tunnel_ids = [t.id for t in tunnels]

    if not target_tunnel_ids:
        return {"flags": []}

    query = (
        db.query(CheckFlag)
        .options(
            joinedload(CheckFlag.user).joinedload(User.worker_profile),
            joinedload(CheckFlag.assignment).joinedload(Assignment.tunnel),
            joinedload(CheckFlag.clearer),
        )
        .join(Assignment, Assignment.id == CheckFlag.assignment_id)
        .filter(
            Assignment.tunnel_id.in_(target_tunnel_ids),
            CheckFlag.user.has(User.company_id == user.company_id),
        )
    )

    if status_filter == "open":
        query = query.filter(CheckFlag.cleared_at.is_(None))

    flags = (
        query.order_by(CheckFlag.raised_at.desc(), CheckFlag.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    overdue_cutoff = now - datetime.timedelta(hours=CHECK_FLAG_OVERDUE_HOURS)
    items = []
    for fl in flags:
        w = fl.user
        emp_no = w.worker_profile.employee_no if w and w.worker_profile else None
        asgn = fl.assignment
        t = asgn.tunnel if asgn else None
        is_overdue = (fl.cleared_at is None and fl.raised_at <= overdue_cutoff)

        items.append(
            {
                "id": fl.id,
                "worker": {
                    "id": w.id if w else fl.user_id,
                    "full_name": w.full_name if w else "Unknown",
                    "employee_no": emp_no,
                },
                "assignment": {
                    "id": asgn.id if asgn else fl.assignment_id,
                    "starts_at": asgn.starts_at.astimezone(datetime.timezone.utc).isoformat() if asgn else "",
                    "ends_at": asgn.ends_at.astimezone(datetime.timezone.utc).isoformat() if asgn else "",
                    "tunnel": {
                        "id": t.id if t else (asgn.tunnel_id if asgn else 0),
                        "name": t.name if t else "Unknown",
                    },
                },
                "raised_at": fl.raised_at.astimezone(datetime.timezone.utc).isoformat(),
                "overdue": is_overdue,
                "cleared_at": fl.cleared_at.astimezone(datetime.timezone.utc).isoformat() if fl.cleared_at else None,
                "cleared_by_name": fl.clearer.full_name if fl.clearer else None,
                "note": fl.note,
            }
        )

    return {"flags": items}


@router.patch("/api/check-flags/{flag_id}")
def api_clear_check_flag(
    flag_id: int,
    req: ClearFlagRequest,
    user: User = Depends(require_role(*SCANNER_ROLES)),
    db: Session = Depends(get_db),
):
    """Clears an open check flag with a manual review note and corrects tunnel headcount."""
    now = clock.utcnow()
    allowed_mines = get_allowed_mine_ids(db, user)

    flag = (
        db.query(CheckFlag)
        .options(
            joinedload(CheckFlag.assignment).joinedload(Assignment.tunnel),
            joinedload(CheckFlag.user).joinedload(User.worker_profile),
        )
        .filter(CheckFlag.id == flag_id)
        .first()
    )
    if not flag or not flag.user or flag.user.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Check flag not found")

    asgn = flag.assignment
    if not asgn or not asgn.tunnel:
        raise HTTPException(status_code=404, detail="Check flag not found")

    # Scoping check
    if user.role == ROLE_FOREMAN:
        curr_tid = get_current_tunnel_id(db, user.id, now)
        if not curr_tid or asgn.tunnel_id != curr_tid:
            raise HTTPException(status_code=404, detail="Check flag not found")
    else:
        if asgn.tunnel.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Check flag not found")

    try:
        updated_flag = clear_flag(db, user, flag, req.note, now=now)
    except FlagClearError as exc:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)

    w = updated_flag.user
    emp_no = w.worker_profile.employee_no if w and w.worker_profile else None
    t = asgn.tunnel

    return {
        "id": updated_flag.id,
        "worker": {
            "id": w.id if w else updated_flag.user_id,
            "full_name": w.full_name if w else "Unknown",
            "employee_no": emp_no,
        },
        "assignment": {
            "id": asgn.id,
            "starts_at": asgn.starts_at.astimezone(datetime.timezone.utc).isoformat(),
            "ends_at": asgn.ends_at.astimezone(datetime.timezone.utc).isoformat(),
            "tunnel": {"id": t.id, "name": t.name},
        },
        "raised_at": updated_flag.raised_at.astimezone(datetime.timezone.utc).isoformat(),
        "overdue": False,
        "cleared_at": updated_flag.cleared_at.astimezone(datetime.timezone.utc).isoformat() if updated_flag.cleared_at else None,
        "cleared_by_name": user.full_name,
        "note": updated_flag.note,
    }
