"""Shift and worker assignment domain service.

Owns assignment creation, schedule shortening, shift deletion, and weekly hours arithmetic.
Must NOT handle HTTP requests, import FastAPI components, or manipulate UI views.
"""
import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import (
    HOURS_TIMEZONE,
    MAX_SHIFT_HOURS,
    WEEKLY_HOURS_LIMIT,
)
from app.models.assignment import Assignment
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.security.dependencies import get_allowed_mine_ids
from app.security.roles import ROLE_FOREMAN, ROLE_MINER
from app.services.audit import record_audit


class ShiftError(Exception):
    """Base exception for shift domain business logic violations."""

    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class ShiftOverlapError(ShiftError):
    """Raised when an assignment time range overlaps with an existing shift."""

    def __init__(self, clashing_shift: Dict[str, Any]):
        super().__init__(
            detail="Worker already has an overlapping shift in this time range",
            status_code=409,
            code="shift_overlap",
        )
        self.clashing_shift = clashing_shift


def weekly_hours_warnings(
    db: Session,
    user_id: int,
    starts_at: datetime.datetime,
    ends_at: datetime.datetime,
    ignore_assignment_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Calculates weekly shift hours and returns warning objects if limit exceeded.

    Week window: Monday 00:00 to next Monday 00:00 in settings.HOURS_TIMEZONE.
    Any shift crossing a week boundary is split so only the within-week portion is counted.
    """
    tz = ZoneInfo(HOURS_TIMEZONE)
    s_local = starts_at.astimezone(tz)
    e_local = ends_at.astimezone(tz)

    # First Monday 00:00 at or before starts_at
    w_start = s_local.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(
        days=s_local.weekday()
    )

    warnings = []
    limit = float(WEEKLY_HOURS_LIMIT)

    while w_start < e_local:
        w_end = w_start + datetime.timedelta(days=7)
        w_start_utc = w_start.astimezone(datetime.timezone.utc)
        w_end_utc = w_end.astimezone(datetime.timezone.utc)

        # Existing DB assignments overlapping this week
        query = db.query(Assignment).filter(
            Assignment.user_id == user_id,
            Assignment.starts_at < w_end_utc,
            Assignment.ends_at > w_start_utc,
        )
        if ignore_assignment_id:
            query = query.filter(Assignment.id != ignore_assignment_id)
        db_shifts = query.all()

        total_seconds = 0.0

        # Sum existing assignments
        for s in db_shifts:
            c_start = max(s.starts_at, w_start_utc)
            c_end = min(s.ends_at, w_end_utc)
            if c_end > c_start:
                total_seconds += (c_end - c_start).total_seconds()

        # Add the evaluated shift
        c_start = max(starts_at, w_start_utc)
        c_end = min(ends_at, w_end_utc)
        if c_end > c_start:
            total_seconds += (c_end - c_start).total_seconds()

        total_hours = round(total_seconds / 3600.0, 1)
        if total_hours > limit:
            warnings.append(
                {
                    "code": "weekly_hours_exceeded",
                    "week_start": w_start.isoformat(),
                    "total_hours": total_hours,
                    "limit_hours": limit,
                }
            )

        w_start = w_end

    return warnings


def create_shift(
    db: Session,
    actor: User,
    user_id: int,
    tunnel_id: int,
    starts_at: datetime.datetime,
    ends_at: datetime.datetime,
) -> Tuple[Assignment, List[Dict[str, Any]]]:
    """Creates a new worker assignment to an underground tunnel.

    Enforces active worker eligibility, mine scoping, duration caps, overlap protection,
    audit persistence, and weekly hours warnings.
    """
    if ends_at <= starts_at:
        raise ShiftError("Shift end time must be after start time", status_code=422)

    duration_hours = (ends_at - starts_at).total_seconds() / 3600.0
    if duration_hours > MAX_SHIFT_HOURS:
        raise ShiftError(
            f"Shift duration ({duration_hours:.1f}h) exceeds maximum allowed ({MAX_SHIFT_HOURS:.1f}h)",
            status_code=422,
        )

    # 1. Target worker validation
    worker = db.query(User).filter(User.id == user_id).first()
    if not worker or worker.company_id != actor.company_id:
        raise ShiftError("Worker not found", status_code=404)
    if worker.role not in (ROLE_FOREMAN, ROLE_MINER) or not worker.active:
        raise ShiftError("User is not an active worker", status_code=400)

    # 2. Tunnel validation and scoping
    allowed_mine_ids = get_allowed_mine_ids(db, actor)
    tunnel = db.query(Tunnel).filter(Tunnel.id == tunnel_id).first()
    if not tunnel or tunnel.mine_id not in allowed_mine_ids:
        raise ShiftError("Tunnel not found", status_code=404)
    if tunnel.status == "closed":
        raise ShiftError("Cannot assign shifts to a closed tunnel", status_code=400, code="tunnel_closed")

    # 3. Worker assigned to tunnel's mine check
    is_in_mine = (
        db.query(UserMine)
        .filter(UserMine.user_id == user_id, UserMine.mine_id == tunnel.mine_id)
        .first()
    )
    if not is_in_mine:
        raise ShiftError(
            "Worker is not assigned to this mine",
            status_code=400,
            code="worker_not_in_mine",
        )

    # 4. Overlap pre-check query
    clashing = (
        db.query(Assignment)
        .filter(
            Assignment.user_id == user_id,
            Assignment.starts_at < ends_at,
            Assignment.ends_at > starts_at,
        )
        .first()
    )
    if clashing:
        t_name = clashing.tunnel.name if clashing.tunnel else f"Tunnel {clashing.tunnel_id}"
        raise ShiftOverlapError(
            clashing_shift={
                "id": clashing.id,
                "tunnel_id": clashing.tunnel_id,
                "tunnel_name": t_name,
                "starts_at": clashing.starts_at.astimezone(datetime.timezone.utc).isoformat(),
                "ends_at": clashing.ends_at.astimezone(datetime.timezone.utc).isoformat(),
            }
        )

    # 5. Insert with exclusion constraint catch
    assignment = Assignment(
        user_id=user_id,
        tunnel_id=tunnel_id,
        starts_at=starts_at,
        ends_at=ends_at,
        created_by=actor.id,
    )
    db.add(assignment)

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Find the clashing shift from the concurrent insert
        clash = (
            db.query(Assignment)
            .filter(
                Assignment.user_id == user_id,
                Assignment.starts_at < ends_at,
                Assignment.ends_at > starts_at,
            )
            .first()
        )
        t_name = clash.tunnel.name if clash and clash.tunnel else f"Tunnel {tunnel_id}"
        raise ShiftOverlapError(
            clashing_shift={
                "id": clash.id if clash else 0,
                "tunnel_id": clash.tunnel_id if clash else tunnel_id,
                "tunnel_name": t_name,
                "starts_at": (
                    clash.starts_at.astimezone(datetime.timezone.utc).isoformat()
                    if clash
                    else starts_at.isoformat()
                ),
                "ends_at": (
                    clash.ends_at.astimezone(datetime.timezone.utc).isoformat()
                    if clash
                    else ends_at.isoformat()
                ),
            }
        ) from exc

    # 6. Audit record in same transaction
    record_audit(
        db,
        actor,
        action="assignment.create",
        entity="assignment",
        entity_id=assignment.id,
        detail={
            "user_id": user_id,
            "tunnel_id": tunnel_id,
            "starts_at": starts_at.astimezone(datetime.timezone.utc).isoformat(),
            "ends_at": ends_at.astimezone(datetime.timezone.utc).isoformat(),
        },
    )
    db.commit()
    db.refresh(assignment)

    # 7. Calculate weekly warnings (non-blocking)
    warnings = weekly_hours_warnings(
        db, user_id, starts_at, ends_at, ignore_assignment_id=assignment.id
    )

    return assignment, warnings


def update_end(
    db: Session,
    actor: User,
    assignment: Assignment,
    new_end: datetime.datetime,
) -> Assignment:
    """Shortens an existing shift end time (end_shift_early)."""
    allowed_mine_ids = get_allowed_mine_ids(db, actor)
    if not assignment.tunnel or assignment.tunnel.mine_id not in allowed_mine_ids:
        raise ShiftError("Assignment not found", status_code=404)

    if new_end <= assignment.starts_at:
        raise ShiftError("New end time must be after shift start time", status_code=422)
    if new_end > assignment.ends_at:
        raise ShiftError("Shift end time can only be shortened, not extended", status_code=422)

    assignment.ends_at = new_end
    record_audit(
        db,
        actor,
        action="assignment.update",
        entity="assignment",
        entity_id=assignment.id,
        detail={"new_ends_at": new_end.astimezone(datetime.timezone.utc).isoformat()},
    )
    db.commit()
    db.refresh(assignment)
    return assignment


def delete_shift(
    db: Session,
    actor: User,
    assignment: Assignment,
    now: Optional[datetime.datetime] = None,
) -> None:
    """Deletes an upcoming shift. Started shifts cannot be deleted."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    allowed_mine_ids = get_allowed_mine_ids(db, actor)
    if not assignment.tunnel or assignment.tunnel.mine_id not in allowed_mine_ids:
        raise ShiftError("Assignment not found", status_code=404)

    if assignment.starts_at <= now:
        raise ShiftError(
            "Cannot delete a shift that has already started",
            status_code=409,
            code="shift_started",
        )

    record_audit(
        db,
        actor,
        action="assignment.delete",
        entity="assignment",
        entity_id=assignment.id,
        detail={"user_id": assignment.user_id, "tunnel_id": assignment.tunnel_id},
    )
    db.delete(assignment)
    db.commit()
