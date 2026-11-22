"""HTTP router for self-service worker shift inquiries and personal profile inspection.

Owns GET /api/me/shifts and GET /api/me/profile endpoints across all authenticated roles.
Must NOT reveal internal managerial assignment metadata (such as created_by) or modify records.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.assignment import Assignment
from app.models.tunnel import Tunnel
from app.models.user import User
from app.schemas.workers import WorkerShiftsResponse, _validate_tz_aware
from app.security.dependencies import get_current_user
from app.security.roles import ROLE_FOREMAN, ROLE_MINER
from app.services.tunnel_scope import get_current_tunnel_id

router = APIRouter(tags=["me"])


@router.get("/api/me/shifts", response_model=WorkerShiftsResponse)
def api_get_my_shifts(
    from_time: Optional[str] = Query(None, alias="from"),
    to_time: Optional[str] = Query(None, alias="to"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns caller's assigned shifts in a worker-safe view (omitting created_by)."""
    query = (
        db.query(Assignment)
        .join(Tunnel, Tunnel.id == Assignment.tunnel_id)
        .filter(Assignment.user_id == user.id)
    )

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

    shifts = []
    for a in assignments:
        t_name = a.tunnel.name if a.tunnel else f"Tunnel {a.tunnel_id}"
        m_id = a.tunnel.mine_id if a.tunnel else 0
        m_name = a.tunnel.mine.name if a.tunnel and a.tunnel.mine else "Unknown"
        shifts.append(
            {
                "id": a.id,
                "tunnel_id": a.tunnel_id,
                "tunnel_name": t_name,
                "mine_id": m_id,
                "mine_name": m_name,
                "starts_at": a.starts_at.astimezone(datetime.timezone.utc).isoformat(),
                "ends_at": a.ends_at.astimezone(datetime.timezone.utc).isoformat(),
                "created_at": a.created_at.astimezone(datetime.timezone.utc).isoformat(),
            }
        )

    return {"shifts": shifts}


@router.get("/api/me/profile")
def api_get_my_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns caller's own worker profile if worker (foreman/miner), otherwise an empty object."""
    if user.role not in (ROLE_FOREMAN, ROLE_MINER):
        return {}

    profile = user.worker_profile
    curr_tid = get_current_tunnel_id(db, user.id)
    curr_tunnel = None
    if curr_tid:
        t = db.query(Tunnel).filter(Tunnel.id == curr_tid).first()
        if t:
            curr_tunnel = {"id": t.id, "name": t.name, "mine_id": t.mine_id}

    return {
        "user_id": user.id,
        "employee_no": profile.employee_no if profile else None,
        "phone": profile.phone if profile else None,
        "emergency_contact_name": profile.emergency_contact_name if profile else None,
        "emergency_contact_phone": profile.emergency_contact_phone if profile else None,
        "joined_on": profile.joined_on.isoformat() if profile and profile.joined_on else None,
        "current_tunnel": curr_tunnel,
    }
