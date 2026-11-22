"""Worker query and detail inspection service.

Owns role-scoped worker retrieval, search filtering, and worker shift history assembly.
Must NOT handle HTTP requests, import FastAPI components, or mutate worker profiles.
"""
import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.assignment import Assignment
from app.models.qr_token import QRToken
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.security.dependencies import get_allowed_mine_ids
from app.security.roles import ROLE_FOREMAN, ROLE_MANAGER, ROLE_MINER
from app.services import clock
from app.services.tunnel_scope import get_current_tunnel_id


class WorkerQueryError(Exception):
    """Exception for worker lookup violations."""

    def __init__(self, detail: str, status_code: int = 404):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def list_workers(
    db: Session,
    actor: User,
    mine_id: Optional[int] = None,
    tunnel_id: Optional[int] = None,
    q: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
) -> List[Dict[str, Any]]:
    """Lists workers visible to actor with search and tunnel assignment scoping."""
    allowed_mine_ids = get_allowed_mine_ids(db, actor)

    if mine_id is not None:
        if mine_id not in allowed_mine_ids:
            raise WorkerQueryError("Mine not found", status_code=404)

    if tunnel_id is not None:
        tunnel = db.query(Tunnel).filter(Tunnel.id == tunnel_id).first()
        if not tunnel or tunnel.mine_id not in allowed_mine_ids:
            raise WorkerQueryError("Tunnel not found", status_code=404)

    # Base query: workers in the same company with worker_profile eager loaded
    query = (
        db.query(User)
        .options(joinedload(User.worker_profile))
        .outerjoin(WorkerProfile, WorkerProfile.user_id == User.id)
        .filter(
            User.company_id == actor.company_id,
            User.role.in_([ROLE_FOREMAN, ROLE_MINER]),
        )
    )

    # Scoping: non-managers only see workers sharing at least one allowed mine
    if actor.role != ROLE_MANAGER:
        shared_worker_ids = (
            db.query(UserMine.user_id)
            .filter(UserMine.mine_id.in_(allowed_mine_ids))
            .distinct()
            .all()
        )
        allowed_uids = [row[0] for row in shared_worker_ids]
        query = query.filter(User.id.in_(allowed_uids))

    # Mine filter
    if mine_id is not None:
        mine_worker_ids = (
            db.query(UserMine.user_id)
            .filter(UserMine.mine_id == mine_id)
            .distinct()
            .all()
        )
        mine_uids = [row[0] for row in mine_worker_ids]
        query = query.filter(User.id.in_(mine_uids))

    # Search filter (name, username, employee_no)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.full_name.ilike(term),
                User.username.ilike(term),
                WorkerProfile.employee_no.ilike(term),
            )
        )

    workers = query.order_by(User.id.asc()).all()

    # Pre-fetch tunnels for current_tunnel resolution
    tunnels_map = {t.id: t for t in db.query(Tunnel).filter(Tunnel.mine_id.in_(allowed_mine_ids)).all()}

    # Batch query active QR badges
    active_tokens = (
        db.query(QRToken.user_id, QRToken.issued_at)
        .filter(
            QRToken.user_id.in_([w.id for w in workers]),
            QRToken.revoked_at.is_(None),
        )
        .all()
    ) if workers else []
    active_badges = {row[0]: row[1] for row in active_tokens}

    results = []
    for w in workers:
        curr_tid = get_current_tunnel_id(db, w.id, now)

        # Tunnel filter
        if tunnel_id is not None and curr_tid != tunnel_id:
            continue

        curr_tunnel_dict = None
        if curr_tid and curr_tid in tunnels_map:
            t = tunnels_map[curr_tid]
            curr_tunnel_dict = {"id": t.id, "name": t.name, "mine_id": t.mine_id}
        elif curr_tid:
            t = db.query(Tunnel).filter(Tunnel.id == curr_tid).first()
            if t:
                curr_tunnel_dict = {"id": t.id, "name": t.name, "mine_id": t.mine_id}

        emp_no = w.worker_profile.employee_no if w.worker_profile else None
        issued = active_badges.get(w.id)
        results.append(
            {
                "id": w.id,
                "username": w.username,
                "full_name": w.full_name,
                "role": w.role,
                "active": w.active,
                "employee_no": emp_no,
                "has_badge": issued is not None,
                "badge_issued_at": issued.astimezone(datetime.timezone.utc).isoformat() if issued else None,
                "current_tunnel": curr_tunnel_dict,
            }
        )

    return results


def get_worker_detail(
    db: Session,
    actor: User,
    worker_id: int,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Returns comprehensive worker profile and recent shift history."""
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    allowed_mine_ids = get_allowed_mine_ids(db, actor)

    worker = (
        db.query(User)
        .options(joinedload(User.worker_profile))
        .filter(
            User.id == worker_id,
            User.company_id == actor.company_id,
            User.role.in_([ROLE_FOREMAN, ROLE_MINER]),
        )
        .first()
    )
    if not worker:
        raise WorkerQueryError("Worker not found", status_code=404)

    # Scoping check for non-managers
    if actor.role != ROLE_MANAGER:
        shares_mine = (
            db.query(UserMine)
            .filter(UserMine.user_id == worker.id, UserMine.mine_id.in_(allowed_mine_ids))
            .first()
        )
        if not shares_mine:
            raise WorkerQueryError("Worker not found", status_code=404)

    curr_tid = get_current_tunnel_id(db, worker.id, now)
    curr_tunnel_dict = None
    if curr_tid:
        t = db.query(Tunnel).filter(Tunnel.id == curr_tid).first()
        if t:
            curr_tunnel_dict = {"id": t.id, "name": t.name, "mine_id": t.mine_id}

    active_token = (
        db.query(QRToken)
        .filter(QRToken.user_id == worker.id, QRToken.revoked_at.is_(None))
        .first()
    )
    has_badge = active_token is not None
    badge_issued_at = (
        active_token.issued_at.astimezone(datetime.timezone.utc).isoformat()
        if active_token
        else None
    )

    profile = worker.worker_profile
    emp_no = profile.employee_no if profile else None
    phone = profile.phone if profile else None
    em_name = profile.emergency_contact_name if profile else None
    em_phone = profile.emergency_contact_phone if profile else None
    joined_on = profile.joined_on.isoformat() if profile and profile.joined_on else None

    # Upcoming shifts: starts_at > now (eager-load tunnel and tunnel.mine)
    upcoming_shifts = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.tunnel).joinedload(Tunnel.mine),
        )
        .filter(Assignment.user_id == worker.id, Assignment.starts_at > now)
        .order_by(Assignment.starts_at.asc())
        .all()
    )

    # Past shifts: ends_at <= now and within 30 days (eager-load tunnel and tunnel.mine)
    thirty_days_ago = now - datetime.timedelta(days=30)
    past_shifts = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.tunnel).joinedload(Tunnel.mine),
        )
        .filter(
            Assignment.user_id == worker.id,
            Assignment.ends_at <= now,
            Assignment.ends_at >= thirty_days_ago,
        )
        .order_by(Assignment.starts_at.desc())
        .all()
    )

    def serialize_assignment(a: Assignment) -> Dict[str, Any]:
        return {
            "id": a.id,
            "tunnel_id": a.tunnel_id,
            "tunnel_name": a.tunnel.name if a.tunnel else f"Tunnel {a.tunnel_id}",
            "mine_id": a.tunnel.mine_id if a.tunnel else 0,
            "mine_name": a.tunnel.mine.name if a.tunnel and a.tunnel.mine else "Unknown",
            "starts_at": a.starts_at.astimezone(datetime.timezone.utc).isoformat(),
            "ends_at": a.ends_at.astimezone(datetime.timezone.utc).isoformat(),
            "created_by": a.created_by,
            "created_at": a.created_at.astimezone(datetime.timezone.utc).isoformat(),
        }

    return {
        "id": worker.id,
        "username": worker.username,
        "full_name": worker.full_name,
        "role": worker.role,
        "active": worker.active,
        "employee_no": emp_no,
        "has_badge": has_badge,
        "badge_issued_at": badge_issued_at,
        "phone": phone,
        "emergency_contact_name": em_name,
        "emergency_contact_phone": em_phone,
        "joined_on": joined_on,
        "current_tunnel": curr_tunnel_dict,
        "upcoming_shifts": [serialize_assignment(s) for s in upcoming_shifts],
        "past_shifts": [serialize_assignment(s) for s in past_shifts],
    }
