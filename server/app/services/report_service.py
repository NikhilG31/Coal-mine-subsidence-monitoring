"""Worker reports and hazard reporting domain service.

Owns report creation, user rate limiting, superior acknowledgment, resolution lifecycle, and scoped queries.
Must NOT handle HTTP requests, import FastAPI components, or format web presentation layouts.
"""
import datetime
from collections import deque
from typing import Deque, Dict, List, Optional
from sqlalchemy.orm import Session, joinedload

from app.config import (
    REPORT_MAX_AGE_DAYS_WORKER_VIEW,
    REPORT_RATE_PER_HOUR,
)
from app.models.mine import Mine
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.models.user import User
from app.models.worker_report import WorkerReport
from app.security.dependencies import get_allowed_mine_ids
from app.security.roles import SUPERIOR_ROLES
from app.services.audit import record_audit
from app.services import clock
from app.services.tunnel_scope import get_current_tunnel_id

# In-memory sliding window rate limiter: user_id -> deque of submission timestamps
_USER_REPORT_TIMESTAMPS: Dict[int, Deque[float]] = {}

VALID_REPORT_TYPES = ("injury", "equipment_fault", "gas_smell", "need_help", "sos")
VALID_SEVERITIES = ("low", "medium", "high", "critical")


class ReportError(Exception):
    """Base exception for report domain logic violations."""

    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class ReportConflictError(ReportError):
    """Raised when an invalid status transition is requested."""

    def __init__(self, detail: str = "Invalid status transition", code: str = "invalid_transition"):
        super().__init__(detail=detail, status_code=409, code=code)


class ReportRateLimitError(ReportError):
    """Raised when a user exceeds the submission rate limit."""

    def __init__(self, detail: str = "Report rate limit exceeded. Please wait before submitting another report.", retry_after: int = 3600):
        super().__init__(detail=detail, status_code=429, code="rate_limit_exceeded")
        self.retry_after = retry_after


def _check_rate_limit(user_id: int, now: datetime.datetime) -> None:
    now_ts = now.timestamp()
    window_cutoff = now_ts - 3600.0

    if user_id not in _USER_REPORT_TIMESTAMPS:
        _USER_REPORT_TIMESTAMPS[user_id] = deque()

    timestamps = _USER_REPORT_TIMESTAMPS[user_id]
    while timestamps and timestamps[0] < window_cutoff:
        timestamps.popleft()

    if len(timestamps) >= REPORT_RATE_PER_HOUR:
        oldest = timestamps[0]
        retry_after = max(1, int(oldest + 3600.0 - now_ts))
        raise ReportRateLimitError(retry_after=retry_after)

    timestamps.append(now_ts)


def create_worker_report(
    db: Session,
    user: User,
    type: str,
    severity: str,
    tunnel_id: Optional[int] = None,
    description: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
) -> WorkerReport:
    """Creates a worker-submitted report under the user's company and mine scope."""
    if now is None:
        now = clock.utcnow()

    _check_rate_limit(user.id, now)

    if type not in VALID_REPORT_TYPES:
        raise ReportError(f"Invalid report type '{type}'", status_code=422)

    # type 'sos' forces severity 'critical'
    if type == "sos":
        severity = "critical"
    elif severity not in VALID_SEVERITIES:
        raise ReportError(f"Invalid severity '{severity}'", status_code=422)

    # Validate and resolve tunnel
    resolved_tunnel: Optional[Tunnel] = None
    if tunnel_id is not None:
        tunnel = (
            db.query(Tunnel)
            .join(Mine, Tunnel.mine_id == Mine.id)
            .filter(
                Tunnel.id == tunnel_id,
                Mine.company_id == user.company_id,
            )
            .first()
        )
        allowed_mines = get_allowed_mine_ids(db, user)
        if not tunnel or (allowed_mines is not None and tunnel.mine_id not in allowed_mines):
            raise ReportError("Tunnel not found", status_code=404)
        resolved_tunnel = tunnel
    else:
        # Fallback to current active/assigned tunnel
        curr_tun_id = get_current_tunnel_id(db, user.id, now)
        if curr_tun_id is not None:
            resolved_tunnel = (
                db.query(Tunnel)
                .join(Mine, Tunnel.mine_id == Mine.id)
                .filter(
                    Tunnel.id == curr_tun_id,
                    Mine.company_id == user.company_id,
                )
                .first()
            )

    # Resolve mine_id
    if resolved_tunnel:
        mine_id = resolved_tunnel.mine_id
    else:
        allowed_mines = get_allowed_mine_ids(db, user)
        if allowed_mines is not None:
            if len(allowed_mines) == 1:
                mine_id = list(allowed_mines)[0]
            else:
                raise ReportError("Mine ID is required when ambiguous", status_code=400, code="mine_required")
        else:
            # Manager without explicit user_mines
            company_mines = db.query(Mine.id).filter(Mine.company_id == user.company_id).all()
            if len(company_mines) == 1:
                mine_id = company_mines[0][0]
            else:
                raise ReportError("Mine ID is required when ambiguous", status_code=400, code="mine_required")

    cleaned_desc: Optional[str] = None
    if description:
        # Strip control characters, truncate to 1000, and trim
        filtered = "".join(c for c in description if c >= " " or c in "\n\r\t")
        cleaned_desc = filtered[:1000].strip() or None

    report = WorkerReport(
        mine_id=mine_id,
        tunnel_id=resolved_tunnel.id if resolved_tunnel else None,
        user_id=user.id,
        node_id=None,
        source="worker",
        type=type,
        severity=severity,
        description=cleaned_desc,
        status="open",
        created_at=now,
        last_signal_at=now,
        repeat_count=1,
    )
    db.add(report)
    db.flush()
    return report


def acknowledge_report(
    db: Session,
    actor: User,
    report_id: int,
    now: Optional[datetime.datetime] = None,
) -> WorkerReport:
    """Acknowledges an open report (superiors only, row locked, audited)."""
    if now is None:
        now = clock.utcnow()

    report = (
        db.query(WorkerReport)
        .options(joinedload(WorkerReport.mine))
        .filter(WorkerReport.id == report_id)
        .with_for_update(of=WorkerReport)
        .first()
    )
    if not report:
        raise ReportError("Report not found", status_code=404)

    allowed_mines = get_allowed_mine_ids(db, actor)
    if (
        report.mine.company_id != actor.company_id
        or (allowed_mines is not None and report.mine_id not in allowed_mines)
    ):
        raise ReportError("Report not found", status_code=404)

    if report.status != "open":
        raise ReportConflictError("Invalid status transition: report is already acknowledged or resolved")

    report.status = "acknowledged"
    report.acknowledged_by = actor.id
    report.acknowledged_at = now
    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="report.acknowledge",
        entity="report",
        entity_id=report.id,
        detail={"report_id": report.id, "status": "acknowledged"},
        company_id=report.mine.company_id,
    )
    return report


def resolve_report(
    db: Session,
    actor: User,
    report_id: int,
    resolution_note: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
) -> WorkerReport:
    """Resolves an open or acknowledged report (superiors only, row locked, audited)."""
    if now is None:
        now = clock.utcnow()

    report = (
        db.query(WorkerReport)
        .options(joinedload(WorkerReport.mine))
        .filter(WorkerReport.id == report_id)
        .with_for_update(of=WorkerReport)
        .first()
    )
    if not report:
        raise ReportError("Report not found", status_code=404)

    allowed_mines = get_allowed_mine_ids(db, actor)
    if (
        report.mine.company_id != actor.company_id
        or (allowed_mines is not None and report.mine_id not in allowed_mines)
    ):
        raise ReportError("Report not found", status_code=404)

    if report.status == "resolved":
        raise ReportConflictError("Invalid status transition: report is already resolved")

    # Resolving from 'open' records acknowledgement too (same actor and timestamp)
    if report.status == "open":
        report.acknowledged_by = actor.id
        report.acknowledged_at = now

    report.status = "resolved"
    report.resolved_by = actor.id
    report.resolved_at = now
    if resolution_note:
        filtered_note = "".join(c for c in resolution_note if c >= " " or c in "\n\r\t")
        report.resolution_note = filtered_note[:500].strip() or None
    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="report.resolve",
        entity="report",
        entity_id=report.id,
        detail={"report_id": report.id, "status": "resolved"},
        company_id=report.mine.company_id,
    )
    return report


def list_reports(
    db: Session,
    user: User,
    status: Optional[str] = None,
    type: Optional[str] = None,
    mine_id: Optional[int] = None,
    tunnel_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    now: Optional[datetime.datetime] = None,
) -> List[WorkerReport]:
    """Retrieves reports scoped by caller role and tenancy with eager loading."""
    if now is None:
        now = clock.utcnow()

    query = (
        db.query(WorkerReport)
        .options(
            joinedload(WorkerReport.mine),
            joinedload(WorkerReport.tunnel),
            joinedload(WorkerReport.reporter).joinedload(User.worker_profile),
            joinedload(WorkerReport.node).joinedload(Node.gateway),
            joinedload(WorkerReport.acknowledger),
            joinedload(WorkerReport.resolver),
            joinedload(WorkerReport.incident),
        )
        .join(Mine, WorkerReport.mine_id == Mine.id)
        .filter(Mine.company_id == user.company_id)
    )

    allowed_mines = get_allowed_mine_ids(db, user)

    if user.role in SUPERIOR_ROLES:
        if allowed_mines is not None:
            query = query.filter(WorkerReport.mine_id.in_(allowed_mines))
        if mine_id is not None:
            if allowed_mines is not None and mine_id not in allowed_mines:
                raise ReportError("Mine not found", status_code=404)
            query = query.filter(WorkerReport.mine_id == mine_id)
    else:
        # Workers (foreman, miner) see ONLY their own reports within REPORT_MAX_AGE_DAYS_WORKER_VIEW
        age_cutoff = now - datetime.timedelta(days=REPORT_MAX_AGE_DAYS_WORKER_VIEW)
        query = query.filter(
            WorkerReport.user_id == user.id,
            WorkerReport.created_at >= age_cutoff,
        )

    if status:
        query = query.filter(WorkerReport.status == status)
    if type:
        query = query.filter(WorkerReport.type == type)
    if tunnel_id is not None:
        query = query.filter(WorkerReport.tunnel_id == tunnel_id)

    return (
        query.order_by(WorkerReport.created_at.desc(), WorkerReport.id.desc())
        .offset(offset)
        .limit(min(max(limit, 1), 500))
        .all()
    )
