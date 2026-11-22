"""Worker tunnel scoping service.

Owns calculation of a worker's current tunnel assignment and visible tunnel IDs.
Must NOT handle HTTP requests, modify database records, or format API responses.
"""
import datetime
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.assignment import Assignment
from app.models.user import User
from app.services import clock


def get_current_tunnel_id(
    db: Session,
    user_id: int,
    now: Optional[datetime.datetime] = None,
) -> Optional[int]:
    """Calculates the current tunnel assignment ID for a worker.

    Single source of truth definition (B0):
    1. Active assignment now (starts_at <= now < ends_at)
    2. Else next upcoming assignment (smallest starts_at > now)
    3. Else most recent past assignment (largest ends_at <= now)
    4. Else None.
    This implements the specification's 'own tunnel' scoping rule.
    """
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    # 1. Active assignment
    active = (
        db.query(Assignment.tunnel_id)
        .filter(
            Assignment.user_id == user_id,
            Assignment.starts_at <= now,
            Assignment.ends_at > now,
        )
        .first()
    )
    if active:
        return active[0]

    # 2. Next upcoming assignment
    upcoming = (
        db.query(Assignment.tunnel_id)
        .filter(
            Assignment.user_id == user_id,
            Assignment.starts_at > now,
        )
        .order_by(Assignment.starts_at.asc())
        .first()
    )
    if upcoming:
        return upcoming[0]

    # 3. Most recent past assignment
    past = (
        db.query(Assignment.tunnel_id)
        .filter(
            Assignment.user_id == user_id,
            Assignment.ends_at <= now,
        )
        .order_by(Assignment.ends_at.desc())
        .first()
    )
    if past:
        return past[0]

    return None


def get_visible_tunnel_ids(
    db: Session,
    user: User,
    now: Optional[datetime.datetime] = None,
) -> Optional[List[int]]:
    """Returns accessible tunnel IDs for the user.

    Miner: [current_tunnel_id] if assigned, or [] if unassigned.
    Foreman and superiors: None (unfiltered tunnel access across allowed mines).
    """
    if user.role == "miner":
        curr = get_current_tunnel_id(db, user.id, now)
        return [curr] if curr is not None else []
    return None
