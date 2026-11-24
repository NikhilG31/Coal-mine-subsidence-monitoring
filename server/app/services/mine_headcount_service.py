"""Mine tunnel headcount and shift lookahead domain service.

Owns aggregation of live inside counts, scheduled active assignments, and upcoming shifts per tunnel.
Must NOT handle HTTP requests, import FastAPI components, or mutate assignment records.
"""
import datetime
from typing import Any, Dict, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import SHIFT_LOOKAHEAD_H
from app.models.assignment import Assignment
from app.models.mine import Mine
from app.models.tunnel import Tunnel
from app.services import clock
from app.services.headcount import inside_now


def headcount_by_tunnel_and_shift(
    db: Session,
    mine: Mine,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Calculates headcount by shift and tunnel for a mine in constant queries.

    Per B0:
    - inside: workers whose latest check event is 'in' (from services/headcount)
    - scheduled_now: assignments active now (starts_at <= now < ends_at)
    - upcoming: assignments starting within the next SHIFT_LOOKAHEAD_H hours
    - mine totals
    """
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    tunnels = (
        db.query(Tunnel)
        .filter(Tunnel.mine_id == mine.id)
        .order_by(Tunnel.name.asc(), Tunnel.id.asc())
        .all()
    )
    tunnel_ids = [t.id for t in tunnels]

    if not tunnel_ids:
        return {
            "totals": {"inside": 0, "scheduled_now": 0, "upcoming": 0},
            "tunnels": [],
        }

    # 1. Inside workers (constant query via inside_now)
    inside_map = inside_now(db, tunnel_ids)

    # 2. Scheduled active assignments now (constant query)
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

    # 3. Upcoming assignments within next SHIFT_LOOKAHEAD_H hours (constant query)
    lookahead_limit = now + datetime.timedelta(hours=SHIFT_LOOKAHEAD_H)
    upcoming_counts = (
        db.query(Assignment.tunnel_id, func.count(Assignment.id))
        .filter(
            Assignment.tunnel_id.in_(tunnel_ids),
            Assignment.starts_at > now,
            Assignment.starts_at <= lookahead_limit,
        )
        .group_by(Assignment.tunnel_id)
        .all()
    )
    upcoming_map = {tid: count for tid, count in upcoming_counts}

    tunnels_list = []
    total_inside = 0
    total_scheduled = 0
    total_upcoming = 0

    for t in tunnels:
        in_c = len(inside_map.get(t.id, []))
        sched_c = scheduled_map.get(t.id, 0)
        up_c = upcoming_map.get(t.id, 0)

        total_inside += in_c
        total_scheduled += sched_c
        total_upcoming += up_c

        tunnels_list.append(
            {
                "tunnel_id": t.id,
                "tunnel_name": t.name,
                "inside": in_c,
                "scheduled_now": sched_c,
                "upcoming": up_c,
            }
        )

    return {
        "totals": {
            "inside": total_inside,
            "scheduled_now": total_scheduled,
            "upcoming": total_upcoming,
        },
        "tunnels": tunnels_list,
    }
