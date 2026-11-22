"""Underground tunnel headcount and personnel location service.

Owns calculation of who is inside each tunnel via DISTINCT ON check events, and scheduled capacity.
Must NOT handle HTTP requests, import FastAPI components, or mutate check records.
"""
import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.assignment import Assignment
from app.models.check_event import CheckEvent
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.services import clock


def get_latest_check_events(
    db: Session,
    user_ids: Optional[List[int]] = None,
) -> List[CheckEvent]:
    """Returns the latest check event for each user using PostgreSQL DISTINCT ON.

    Single source of truth (B0): ordered by scanned_at DESC, then id DESC.
    """
    query = (
        db.query(CheckEvent)
        .distinct(CheckEvent.user_id)
        .options(
            joinedload(CheckEvent.user).joinedload(User.worker_profile),
            joinedload(CheckEvent.tunnel).joinedload(Tunnel.mine),
        )
        .order_by(CheckEvent.user_id, CheckEvent.scanned_at.desc(), CheckEvent.id.desc())
    )
    if user_ids is not None:
        query = query.filter(CheckEvent.user_id.in_(user_ids))
    return query.all()


def get_worker_latest_event(db: Session, user_id: int) -> Optional[CheckEvent]:
    """Returns the latest check event for a single worker, or None."""
    return (
        db.query(CheckEvent)
        .options(
            joinedload(CheckEvent.tunnel),
        )
        .filter(CheckEvent.user_id == user_id)
        .order_by(CheckEvent.scanned_at.desc(), CheckEvent.id.desc())
        .first()
    )


def inside_now(
    db: Session,
    tunnel_ids: List[int],
) -> Dict[int, List[Dict[str, Any]]]:
    """Returns workers currently inside the given tunnels mapped by tunnel_id.

    A worker is inside a tunnel iff their latest event has direction 'in'.
    Restricts evaluation to workers who belong (user_mines) to the mines of the requested tunnels.
    """
    if not tunnel_ids:
        return {}

    tunnels = db.query(Tunnel).filter(Tunnel.id.in_(tunnel_ids)).all()
    mine_ids = list({t.mine_id for t in tunnels})
    if not mine_ids:
        return {tid: [] for tid in tunnel_ids}

    allowed_worker_ids = (
        db.query(UserMine.user_id)
        .filter(UserMine.mine_id.in_(mine_ids))
        .distinct()
        .subquery()
    )

    latest_events = (
        db.query(CheckEvent)
        .distinct(CheckEvent.user_id)
        .options(
            joinedload(CheckEvent.user).joinedload(User.worker_profile),
        )
        .filter(CheckEvent.user_id.in_(db.query(allowed_worker_ids.c.user_id)))
        .order_by(CheckEvent.user_id, CheckEvent.scanned_at.desc(), CheckEvent.id.desc())
        .all()
    )
    result: Dict[int, List[Dict[str, Any]]] = {tid: [] for tid in tunnel_ids}

    for ev in latest_events:
        if ev.direction == "in" and ev.tunnel_id in result:
            w = ev.user
            emp_no = w.worker_profile.employee_no if w and w.worker_profile else None
            result[ev.tunnel_id].append(
                {
                    "id": w.id if w else ev.user_id,
                    "full_name": w.full_name if w else "Unknown",
                    "employee_no": emp_no,
                    "scanned_at": ev.scanned_at.astimezone(datetime.timezone.utc).isoformat(),
                }
            )

    return result


def headcount_by_tunnel(
    db: Session,
    tunnel_ids: List[int],
    now: Optional[datetime.datetime] = None,
) -> Dict[int, Dict[str, Any]]:
    """Calculates live headcount and active scheduled worker counts per tunnel.

    Returns:
        Dict[tunnel_id, {"tunnel_id": int, "tunnel_name": str, "mine_id": int, "inside": int, "scheduled_now": int, "workers": List[dict]}]
    """
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    if not tunnel_ids:
        return {}

    tunnels = db.query(Tunnel).filter(Tunnel.id.in_(tunnel_ids)).all()
    tunnels_map = {t.id: t for t in tunnels}

    # 1. Inside workers by tunnel
    inside_workers = inside_now(db, tunnel_ids)

    # 2. Scheduled active assignments now (constant single query)
    scheduled_counts = (
        db.query(Assignment.tunnel_id, func.count(Assignment.id))
        .filter(
            Assignment.tunnel_id.in_(tunnel_ids),
            Assignment.starts_at <= now,
            Assignment.ends_at > now,
        )
        .group_by(Assignment.tunnel_id)
        .all()
    )
    scheduled_map = {tid: count for tid, count in scheduled_counts}

    headcount_map = {}
    for tid in tunnel_ids:
        t = tunnels_map.get(tid)
        t_name = t.name if t else f"Tunnel {tid}"
        m_id = t.mine_id if t else 0
        workers = inside_workers.get(tid, [])
        headcount_map[tid] = {
            "tunnel_id": tid,
            "tunnel_name": t_name,
            "mine_id": m_id,
            "inside": len(workers),
            "scheduled_now": scheduled_map.get(tid, 0),
            "workers": workers,
        }

    return headcount_map
