"""Shift and worker assignment domain service.

Owns assignment creation, schedule shortening, shift deletion, and shift conflict validation.
Must NOT handle HTTP requests, import FastAPI components, or manipulate UI views.
"""
import datetime
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload

from app.config import MAX_SHIFT_HOURS
from app.models.assignment import Assignment
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.security.dependencies import get_allowed_mine_ids
from app.security.roles import ROLE_FOREMAN, ROLE_MINER
from app.services.audit import record_audit
from app.services import clock
from app.services.weekly_hours import weekly_hours_warnings


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


def _format_clashing_shift(
    clash: Assignment,
    allowed_mine_ids: List[int],
) -> Dict[str, Any]:
    """Formats clashing shift payload, redacting tunnel metadata if outside allowed mines."""
    clash_mine_id = clash.tunnel.mine_id if clash.tunnel else None
    starts_iso = clash.starts_at.astimezone(datetime.timezone.utc).isoformat()
    ends_iso = clash.ends_at.astimezone(datetime.timezone.utc).isoformat()

    if clash_mine_id is None or clash_mine_id not in allowed_mine_ids:
        # Redact tunnel info for mines outside caller's scope
        return {
            "starts_at": starts_iso,
            "ends_at": ends_iso,
        }

    t_name = clash.tunnel.name if clash.tunnel else f"Tunnel {clash.tunnel_id}"
    return {
        "id": clash.id,
        "tunnel_id": clash.tunnel_id,
        "tunnel_name": t_name,
        "starts_at": starts_iso,
        "ends_at": ends_iso,
    }


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
        .options(joinedload(Assignment.tunnel))
        .filter(
            Assignment.user_id == user_id,
            Assignment.starts_at < ends_at,
            Assignment.ends_at > starts_at,
        )
        .first()
    )
    if clashing:
        raise ShiftOverlapError(
            clashing_shift=_format_clashing_shift(clashing, allowed_mine_ids)
        )

    # 5. Insert with exclusion constraint catch using a SAVEPOINT
    assignment = Assignment(
        user_id=user_id,
        tunnel_id=tunnel_id,
        starts_at=starts_at,
        ends_at=ends_at,
        created_by=actor.id,
    )

    try:
        with db.begin_nested():
            db.add(assignment)
            db.flush()
    except (IntegrityError, OperationalError) as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if sqlstate not in ("23P01", "40P01"):
            raise ShiftError("Database integrity violation", status_code=400) from exc

        # Find the clashing shift from the concurrent insert
        clash = (
            db.query(Assignment)
            .options(joinedload(Assignment.tunnel))
            .filter(
                Assignment.user_id == user_id,
                Assignment.starts_at < ends_at,
                Assignment.ends_at > starts_at,
            )
            .first()
        )
        raise ShiftOverlapError(
            clashing_shift=_format_clashing_shift(clash, allowed_mine_ids) if clash else None
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
        now = clock.utcnow()
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
