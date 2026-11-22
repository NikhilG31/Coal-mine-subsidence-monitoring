"""Background shift check-out exception flag evaluation service.

Owns pure evaluation of completed shifts without check-outs and idempotent check_flag creation.
Must NOT handle HTTP requests, import FastAPI components, or mutate shifts or check events.
"""
import datetime
import logging
from typing import Dict, Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import CHECKIN_EARLY_MIN, CHECKOUT_GRACE_MIN
from app.models.assignment import Assignment
from app.models.check_flag import CheckFlag
from app.services.headcount import get_worker_latest_event

logger = logging.getLogger(__name__)


def run_flag_cycle(
    db: Session,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, int]:
    """Evaluates ended shifts and raises check_flags for unclosed underground sessions.

    Pure function with respect to time (accepts injected now).
    Identifies assignments ended at least CHECKOUT_GRACE_MIN ago (up to 7 days back)
    where the worker's latest check event overall is still 'in' for that assignment's tunnel
    and was scanned in the shift check-in window [starts_at - CHECKIN_EARLY_MIN, ends_at).
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    grace_threshold = now - datetime.timedelta(minutes=CHECKOUT_GRACE_MIN)
    seven_days_ago = now - datetime.timedelta(days=7)

    # Candidate assignments: ended past grace period, within 7-day lookback, with no check_flag
    candidate_shifts = (
        db.query(Assignment)
        .outerjoin(CheckFlag, CheckFlag.assignment_id == Assignment.id)
        .filter(
            Assignment.ends_at <= grace_threshold,
            Assignment.ends_at >= seven_days_ago,
            CheckFlag.id.is_(None),
        )
        .all()
    )

    flags_raised = 0

    for asgn in candidate_shifts:
        latest_event = get_worker_latest_event(db, asgn.user_id)
        if not latest_event:
            # Never checked in
            continue

        # Worker must still be 'in' overall, in this shift's tunnel
        if latest_event.direction != "in" or latest_event.tunnel_id != asgn.tunnel_id:
            continue

        # Event must fall within shift check-in window
        early_window = asgn.starts_at - datetime.timedelta(minutes=CHECKIN_EARLY_MIN)
        if not (early_window <= latest_event.scanned_at < asgn.ends_at):
            continue

        # Insert check_flag idempotently inside a savepoint
        flag = CheckFlag(
            user_id=asgn.user_id,
            assignment_id=asgn.id,
            raised_at=now,
        )
        try:
            with db.begin_nested():
                db.add(flag)
                db.flush()
            db.commit()
            flags_raised += 1
            logger.info(
                "Raised check_flag %d for user %d assignment %d",
                flag.id,
                asgn.user_id,
                asgn.id,
            )
        except IntegrityError:
            # Already exists (idempotent guarantee via assignment_id UNIQUE)
            db.rollback()
            continue

    return {"raised": flags_raised}
