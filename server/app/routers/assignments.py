"""HTTP router for shift assignment scheduling and timeline management.

Owns POST/PATCH/DELETE /api/assignments and GET /api/assignments query endpoints.
Must NOT execute raw database transactions or compute weekly schedule mathematics directly.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.assignment import Assignment
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.schemas.workers import (
    AssignmentsListResponse,
    CreateAssignmentRequest,
    CreateAssignmentResponse,
    UpdateAssignmentRequest,
    _validate_tz_aware,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
    security_logger,
)
from app.security.roles import (
    ROLE_MANAGER,
    ROLE_SAFETY_OFFICER,
    SUPERIOR_ROLES,
)
from app.services.shift_service import (
    ShiftError,
    ShiftOverlapError,
    create_shift,
    delete_shift,
    update_end,
)

router = APIRouter(tags=["assignments"])

SCHEDULER_ROLES = (ROLE_MANAGER, ROLE_SAFETY_OFFICER)


def _serialize_assignment(a: Assignment) -> dict:
    w_name = a.user.full_name if a.user else "Unknown"
    emp_no = a.user.worker_profile.employee_no if a.user and a.user.worker_profile else None
    t_name = a.tunnel.name if a.tunnel else f"Tunnel {a.tunnel_id}"
    m_id = a.tunnel.mine_id if a.tunnel else 0
    m_name = a.tunnel.mine.name if a.tunnel and a.tunnel.mine else "Unknown"
    return {
        "id": a.id,
        "user_id": a.user_id,
        "worker_name": w_name,
        "employee_no": emp_no,
        "tunnel_id": a.tunnel_id,
        "tunnel_name": t_name,
        "mine_id": m_id,
        "mine_name": m_name,
        "starts_at": a.starts_at.astimezone(datetime.timezone.utc).isoformat(),
        "ends_at": a.ends_at.astimezone(datetime.timezone.utc).isoformat(),
        "created_by": a.created_by,
        "created_at": a.created_at.astimezone(datetime.timezone.utc).isoformat(),
    }


@router.post("/api/assignments", status_code=status.HTTP_201_CREATED)
def api_create_assignment(
    req: CreateAssignmentRequest,
    user: User = Depends(require_role(*SCHEDULER_ROLES)),
    db: Session = Depends(get_db),
):
    """Schedules a worker to an underground tunnel shift (Manager or Safety Officer)."""
    try:
        assignment, warnings = create_shift(
            db,
            actor=user,
            user_id=req.user_id,
            tunnel_id=req.tunnel_id,
            starts_at=req.starts_at,
            ends_at=req.ends_at,
        )
        security_logger.info(
            "User '%s' assigned worker %d to tunnel %d (%s to %s)",
            user.username,
            req.user_id,
            req.tunnel_id,
            req.starts_at.isoformat(),
            req.ends_at.isoformat(),
        )
        return {
            "assignment": _serialize_assignment(assignment),
            "warnings": warnings,
        }
    except ShiftOverlapError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": exc.detail,
                "code": exc.code,
                "clashing_shift": exc.clashing_shift,
            },
        )
    except ShiftError as exc:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)


@router.patch("/api/assignments/{assignment_id}")
def api_update_assignment(
    assignment_id: int,
    req: UpdateAssignmentRequest,
    user: User = Depends(require_role(*SCHEDULER_ROLES)),
    db: Session = Depends(get_db),
):
    """Shortens an active or upcoming shift end time (Manager or Safety Officer)."""
    allowed_mines = get_allowed_mine_ids(db, user)
    assignment = (
        db.query(Assignment)
        .join(Tunnel, Tunnel.id == Assignment.tunnel_id)
        .filter(
            Assignment.id == assignment_id,
            Tunnel.mine_id.in_(allowed_mines),
        )
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    try:
        updated = update_end(db, actor=user, assignment=assignment, new_end=req.ends_at)
        security_logger.info(
            "User '%s' shortened assignment %d ends_at to %s",
            user.username,
            assignment_id,
            req.ends_at.isoformat(),
        )
        return {"assignment": _serialize_assignment(updated)}
    except ShiftError as exc:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)


@router.delete("/api/assignments/{assignment_id}")
def api_delete_assignment(
    assignment_id: int,
    user: User = Depends(require_role(*SCHEDULER_ROLES)),
    db: Session = Depends(get_db),
):
    """Deletes an unstarted future shift (Manager or Safety Officer)."""
    allowed_mines = get_allowed_mine_ids(db, user)
    assignment = (
        db.query(Assignment)
        .join(Tunnel, Tunnel.id == Assignment.tunnel_id)
        .filter(
            Assignment.id == assignment_id,
            Tunnel.mine_id.in_(allowed_mines),
        )
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    try:
        delete_shift(db, actor=user, assignment=assignment)
        security_logger.info(
            "User '%s' deleted future assignment %d",
            user.username,
            assignment_id,
        )
        return {"message": "Assignment deleted successfully"}
    except ShiftError as exc:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)


@router.get("/api/assignments", response_model=AssignmentsListResponse)
def api_list_assignments(
    user_id: Optional[int] = None,
    mine_id: Optional[int] = None,
    tunnel_id: Optional[int] = None,
    from_time: Optional[str] = Query(None, alias="from"),
    to_time: Optional[str] = Query(None, alias="to"),
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Queries shift assignments within caller's authorized mines and time filters."""
    allowed_mines = get_allowed_mine_ids(db, user)

    if mine_id is not None:
        if mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Mine not found")
        scoped_mines = [mine_id]
    else:
        scoped_mines = allowed_mines

    if tunnel_id is not None:
        t = db.query(Tunnel).filter(Tunnel.id == tunnel_id).first()
        if not t or t.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Tunnel not found")

    query = (
        db.query(Assignment)
        .join(Tunnel, Tunnel.id == Assignment.tunnel_id)
        .filter(Tunnel.mine_id.in_(scoped_mines))
    )

    if tunnel_id is not None:
        query = query.filter(Assignment.tunnel_id == tunnel_id)

    if user_id is not None:
        target_user = (
            db.query(User)
            .filter(User.id == user_id, User.company_id == user.company_id)
            .first()
        )
        if not target_user:
            raise HTTPException(status_code=404, detail="Worker not found")
        if user.role != ROLE_MANAGER:
            shares = (
                db.query(UserMine)
                .filter(UserMine.user_id == user_id, UserMine.mine_id.in_(allowed_mines))
                .first()
            )
            if not shares:
                raise HTTPException(status_code=404, detail="Worker not found")
        query = query.filter(Assignment.user_id == user_id)

    if from_time:
        try:
            from_dt = _validate_tz_aware(from_time)
            query = query.filter(Assignment.ends_at > from_dt)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=f"Invalid 'from' parameter: {err}")

    if to_time:
        try:
            to_dt = _validate_tz_aware(to_time)
            query = query.filter(Assignment.starts_at < to_dt)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=f"Invalid 'to' parameter: {err}")

    assignments = query.order_by(Assignment.starts_at.asc()).all()
    return {"assignments": [_serialize_assignment(a) for a in assignments]}
