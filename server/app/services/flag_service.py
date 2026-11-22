"""Background shift check-out exception flag evaluation service.

Owns pure evaluation of completed shifts without check-outs and idempotent check_flag creation.
Must NOT handle HTTP requests, import FastAPI components, or mutate shifts or check events.
"""
import datetime
import logging
from collections import defaultdict
from typing import Dict, List, Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import CHECKIN_EARLY_MIN, CHECKOUT_GRACE_MIN
from app.models.assignment import Assignment
from app.models.check_event import CheckEvent
from app.models.check_flag import CheckFlag
from app.services import clock

logger = logging.getLogger(__name__)


def run_flag_cycle(
    db: Session,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, int]:
    """Evaluates ended shifts and raises check_flags for unclosed underground sessions.

    Fetches latest events of candidate workers in ONE DISTINCT ON query and pre-filters
    candidate shifts in SQL where the worker's latest event is 'in'.
    Treats consecutive assignments in the same tunnel with gap <= CHECKOUT_GRACE_MIN as ONE chain.
    Evaluates the flag against the LAST shift of the chain.
    """
    if now is None:
        now = utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    grace_delta = datetime.timedelta(minutes=CHECKOUT_GRACE_MIN)
    early_delta = datetime.timedelta(minutes=CHECKIN_EARLY_MIN)
    seven_days_ago = now - datetime.timedelta(days=7)

    # 1. Fetch latest check event for all workers in ONE DISTINCT ON query (A4)
    latest_event_subq = (
        db.query(
            CheckEvent.user_id.label("user_id"),
            CheckEvent.tunnel_id.label("tunnel_id"),
            CheckEvent.direction.label("direction"),
            CheckEvent.scanned_at.label("scanned_at"),
        )
        .distinct(CheckEvent.user_id)
        .order_by(CheckEvent.user_id, CheckEvent.scanned_at.desc(), CheckEvent.id.desc())
        .subquery()
    )

    # Pre-filter in SQL: only workers whose latest event is 'in'
    latest_in_subq = (
        db.query(
            latest_event_subq.c.user_id,
            latest_event_subq.c.tunnel_id,
            latest_event_subq.c.scanned_at,
        )
        .filter(latest_event_subq.c.direction == "in")
        .subquery()
    )

    # 2. Join candidate shifts directly with latest_in_subq (single SQL query)
    candidate_rows = (
        db.query(
            Assignment,
            latest_in_subq.c.scanned_at.label("latest_scanned_at"),
            CheckFlag.id.label("flag_id"),
        )
        .join(
            latest_in_subq,
            (Assignment.user_id == latest_in_subq.c.user_id)
            & (Assignment.tunnel_id == latest_in_subq.c.tunnel_id),
        )
        .outerjoin(CheckFlag, CheckFlag.assignment_id == Assignment.id)
        .filter(
            Assignment.ends_at >= seven_days_ago,
        )
        .order_by(Assignment.user_id, Assignment.tunnel_id, Assignment.starts_at.asc())
        .all()
    )

    # Group assignments by (user_id, tunnel_id)
    worker_tunnel_shifts = defaultdict(list)
    worker_scanned_at = {}
    flagged_assignment_ids = set()

    for asgn, scanned_at, flag_id in candidate_rows:
        key = (asgn.user_id, asgn.tunnel_id)
        worker_tunnel_shifts[key].append(asgn)
        worker_scanned_at[key] = scanned_at
        if flag_id is not None:
            flagged_assignment_ids.add(asgn.id)

    flags_raised = 0

    for (user_id, tunnel_id), shifts in worker_tunnel_shifts.items():
        # Never flag a shift while the worker has another shift active now in that tunnel (A2)
        has_active_shift_now = (
            db.query(Assignment.id)
            .filter(
                Assignment.user_id == user_id,
                Assignment.tunnel_id == tunnel_id,
                Assignment.starts_at <= now,
                Assignment.ends_at > now,
            )
            .first()
            is not None
        )
        if has_active_shift_now:
            continue

        latest_scanned_at = worker_scanned_at[(user_id, tunnel_id)]
        if latest_scanned_at.tzinfo is None:
            latest_scanned_at = latest_scanned_at.replace(tzinfo=datetime.timezone.utc)

        # Build chains of consecutive assignments (gap <= CHECKOUT_GRACE_MIN)
        chains: List[List[Assignment]] = []
        current_chain: List[Assignment] = []

        for asgn in shifts:
            if not current_chain:
                current_chain.append(asgn)
            else:
                prev_asgn = current_chain[-1]
                gap = (asgn.starts_at - prev_asgn.ends_at).total_seconds()
                if 0 <= gap <= CHECKOUT_GRACE_MIN * 60:
                    current_chain.append(asgn)
                else:
                    chains.append(current_chain)
                    current_chain = [asgn]
        if current_chain:
            chains.append(current_chain)

        for chain in chains:
            chain_first = chain[0]
            last_shift = chain[-1]

            # Already flagged?
            if last_shift.id in flagged_assignment_ids:
                continue

            # Must have ended past grace threshold
            if last_shift.ends_at + grace_delta > now:
                continue

            # Event's scanned_at must be within [chain_first_starts_at - CHECKIN_EARLY_MIN, last_ends_at)
            chain_window_start = chain_first.starts_at - early_delta
            if not (chain_window_start <= latest_scanned_at < last_shift.ends_at):
                continue

            # Insert check_flag for the last shift of the chain
            flag = CheckFlag(
                user_id=last_shift.user_id,
                assignment_id=last_shift.id,
                raised_at=now,
            )
            try:
                with db.begin_nested():
                    db.add(flag)
                    db.flush()
                db.commit()
                flags_raised += 1
                flagged_assignment_ids.add(last_shift.id)
                logger.info(
                    "Raised check_flag %d for user %d assignment %d",
                    flag.id,
                    last_shift.user_id,
                    last_shift.id,
                )
            except IntegrityError:
                db.rollback()
                continue

    return {"raised": flags_raised}
