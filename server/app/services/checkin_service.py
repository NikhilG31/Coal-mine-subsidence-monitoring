"""Worker gate check-in and check-out recording service.

Owns QR badge verification, state transitions (in/out), duplicate suppression, and late-clearance.
Must NOT handle HTTP requests, import FastAPI components, or leak worker contact data.
"""
import datetime
import hashlib
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session, joinedload

from app.config import CHECKIN_EARLY_MIN
from app.models.assignment import Assignment
from app.models.check_event import CheckEvent
from app.models.check_flag import CheckFlag
from app.models.qr_token import QRToken
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.security.dependencies import get_allowed_mine_ids
from app.security.roles import ROLE_FOREMAN, ROLE_MINER
from app.services.audit import record_audit
from app.services import clock
from app.services.headcount import get_worker_latest_event
from app.services.incident_service import add_late_arrival, mark_accounted_on_exit
from app.services.tunnel_scope import get_current_tunnel_id


class CheckInError(Exception):
    """Exception raised for check-in/out domain validation errors."""

    def __init__(
        self,
        detail: str,
        status_code: int = 400,
        code: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code
        self.extra = extra or {}


class UnknownBadge(CheckInError):
    """Raised when a badge token is unrecognized, revoked, or belongs to an inactive worker."""

    def __init__(self, detail: str = "Badge not recognised", status_code: int = 404, code: Optional[str] = None):
        super().__init__(detail=detail, status_code=status_code, code=code)


def _resolve_tunnel(
    db: Session,
    scanner: User,
    tunnel_id: Optional[int],
    now: datetime.datetime,
) -> Tunnel:
    """Validates and resolves the target tunnel for the scanner."""
    allowed_mine_ids = get_allowed_mine_ids(db, scanner)

    if scanner.role == ROLE_FOREMAN:
        curr_tunnel_id = get_current_tunnel_id(db, scanner.id, now)
        if not curr_tunnel_id:
            raise CheckInError("Foreman has no active tunnel assignment", status_code=404)
        if tunnel_id is not None and tunnel_id != curr_tunnel_id:
            raise CheckInError("Foreman may only scan for their current tunnel", status_code=403)
        resolved_id = curr_tunnel_id
    else:
        if tunnel_id is None:
            raise CheckInError("tunnel_id is required for superior scanners", status_code=422)
        resolved_id = tunnel_id

    tunnel = db.query(Tunnel).options(joinedload(Tunnel.mine)).filter(Tunnel.id == resolved_id).first()
    if not tunnel or tunnel.mine_id not in allowed_mine_ids:
        raise CheckInError("Tunnel not found", status_code=404)
    return tunnel


def _validate_worker_mine_membership(
    db: Session,
    tunnel: Tunnel,
    worker: User,
) -> None:
    """Asserts worker belongs to the tunnel's mine."""
    shares = (
        db.query(UserMine)
        .filter(UserMine.user_id == worker.id, UserMine.mine_id == tunnel.mine_id)
        .first()
    )
    if not shares:
        raise CheckInError("Worker not found", status_code=404)


def _process_check_event(
    db: Session,
    actor: User,
    worker: User,
    tunnel: Tunnel,
    direction: str,
    method: str,
    now: datetime.datetime,
) -> Dict[str, Any]:
    """Internal core processor for gate check-in and check-out events."""
    # Lock worker row to serialize concurrent gate operations for this worker
    db.query(User.id).filter(User.id == worker.id).with_for_update().first()

    latest_event = get_worker_latest_event(db, worker.id)
    is_inside = (latest_event is not None and latest_event.direction == "in")
    current_inside_tunnel_id = latest_event.tunnel_id if is_inside else None

    # Deduplication within 5 seconds for identical scan
    if (
        latest_event is not None
        and latest_event.direction == direction
        and latest_event.tunnel_id == tunnel.id
        and abs((now - latest_event.scanned_at).total_seconds()) < 5.0
    ):
        emp_no = worker.worker_profile.employee_no if worker.worker_profile else None
        return {
            "duplicate": True,
            "worker": {"id": worker.id, "full_name": worker.full_name, "employee_no": emp_no},
            "direction": direction,
            "tunnel": {"id": tunnel.id, "name": tunnel.name},
            "scanned_at": latest_event.scanned_at.astimezone(datetime.timezone.utc).isoformat(),
            "warnings": [],
        }

    # State transitions
    if direction == "in" and is_inside:
        raise CheckInError(
            "Worker is already checked in",
            status_code=409,
            code="already_checked_in",
            extra={"tunnel_id": current_inside_tunnel_id},
        )
    if direction == "out" and not is_inside:
        raise CheckInError(
            "Worker is not checked in",
            status_code=409,
            code="not_checked_in",
        )

    # Warnings on check-in if no active or imminent shift
    warnings = []
    if direction == "in":
        early_window = now + datetime.timedelta(minutes=CHECKIN_EARLY_MIN)
        has_shift = (
            db.query(Assignment)
            .filter(
                Assignment.user_id == worker.id,
                Assignment.tunnel_id == tunnel.id,
                Assignment.starts_at <= early_window,
                Assignment.ends_at > now,
            )
            .first()
        )
        if not has_shift:
            warnings.append(
                {"code": "no_active_shift", "message": "Worker has no active scheduled shift in this tunnel"}
            )

    # On check-out: automatically clear any open check_flag and mark accounted in open incidents
    if direction == "out":
        open_flags = (
            db.query(CheckFlag)
            .filter(CheckFlag.user_id == worker.id, CheckFlag.cleared_at.is_(None))
            .all()
        )
        for flag in open_flags:
            flag.cleared_at = now
            flag.cleared_by = actor.id
            flag.note = "scanned out late"

        mark_accounted_on_exit(db, scanner=actor, worker=worker, tunnel=tunnel, now=now)
    elif direction == "in":
        add_late_arrival(db, worker=worker, tunnel=tunnel, now=now)

    event = CheckEvent(
        user_id=worker.id,
        tunnel_id=tunnel.id,
        direction=direction,
        method=method,
        scanned_at=now,
        scanned_by=actor.id,
    )
    db.add(event)

    if method == "manual":
        record_audit(
            db,
            actor,
            action="checkin.manual",
            entity="worker",
            entity_id=worker.id,
            detail={"user_id": worker.id, "direction": direction, "tunnel_id": tunnel.id},
        )

    db.commit()
    db.refresh(event)

    emp_no = worker.worker_profile.employee_no if worker.worker_profile else None
    return {
        "duplicate": False,
        "worker": {"id": worker.id, "full_name": worker.full_name, "employee_no": emp_no},
        "direction": direction,
        "tunnel": {"id": tunnel.id, "name": tunnel.name},
        "scanned_at": event.scanned_at.astimezone(datetime.timezone.utc).isoformat(),
        "warnings": warnings,
    }


def record_scan(
    db: Session,
    scanner: User,
    token: str,
    direction: str,
    tunnel_id: Optional[int] = None,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Processes a QR badge scan at the tunnel entrance or exit."""
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    if direction not in ("in", "out"):
        raise CheckInError("Direction must be 'in' or 'out'", status_code=422)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    qr_token = (
        db.query(QRToken)
        .options(joinedload(QRToken.user).joinedload(User.worker_profile))
        .filter(QRToken.token_hash == token_hash)
        .first()
    )
    if not qr_token or qr_token.revoked_at is not None or not qr_token.user or not qr_token.user.active:
        raise UnknownBadge("Badge not recognised", status_code=404)

    worker = qr_token.user
    if worker.role not in (ROLE_FOREMAN, ROLE_MINER):
        raise UnknownBadge("Badge not recognised", status_code=404)

    tunnel = _resolve_tunnel(db, scanner, tunnel_id, now)
    _validate_worker_mine_membership(db, tunnel, worker)

    return _process_check_event(db, scanner, worker, tunnel, direction, "qr", now)


def record_manual(
    db: Session,
    actor: User,
    user_id: int,
    direction: str,
    tunnel_id: Optional[int] = None,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Manually records a check-in or check-out event when badge is lost or unavailable."""
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    if direction not in ("in", "out"):
        raise CheckInError("Direction must be 'in' or 'out'", status_code=422)

    worker = (
        db.query(User)
        .options(joinedload(User.worker_profile))
        .filter(User.id == user_id, User.company_id == actor.company_id)
        .first()
    )
    if not worker or not worker.active or worker.role not in (ROLE_FOREMAN, ROLE_MINER):
        raise CheckInError("Worker not found", status_code=404)

    tunnel = _resolve_tunnel(db, actor, tunnel_id, now)
    _validate_worker_mine_membership(db, tunnel, worker)

    return _process_check_event(db, actor, worker, tunnel, direction, "manual", now)
