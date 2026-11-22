"""Weekly shift hours calculation service.

Owns rolling week boundary splitting, cumulative hours summing, and overtime warning generation.
Must NOT handle HTTP requests, import FastAPI components, or mutate assignment records.
"""
import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session

from app.config import HOURS_TIMEZONE, WEEKLY_HOURS_LIMIT
from app.models.assignment import Assignment


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
