"""Shift check-out exception flag resolution and manual clearance service.

Owns manual flag clearance, headcount correction via manual 'out' event creation, and clearance auditing.
Must NOT handle HTTP requests, import FastAPI components, or leak note text into audit logs.
"""
import datetime
from typing import Optional
from sqlalchemy.orm import Session, joinedload

from app.models.check_event import CheckEvent
from app.models.check_flag import CheckFlag
from app.models.user import User
from app.services.audit import record_audit


class FlagClearError(Exception):
    """Exception raised for check flag clearance violations."""

    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def clear_flag(
    db: Session,
    actor: User,
    flag: CheckFlag,
    note: str,
    now: Optional[datetime.datetime] = None,
) -> CheckFlag:
    """Clears an open check flag with a manual review note and inserts a manual 'out' event.

    Corrects the tunnel headcount by appending a manual 'out' check event for the assignment tunnel.
    Audits the operation recording only the flag ID, strictly omitting note text.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    # 1. Note length validation (3 to 500 characters)
    trimmed_note = (note or "").strip()
    if len(trimmed_note) < 3 or len(trimmed_note) > 500:
        raise FlagClearError(
            "Clearance note must be between 3 and 500 characters",
            status_code=422,
        )

    # 2. Idempotency / conflict check
    if flag.cleared_at is not None:
        raise FlagClearError(
            "Check flag has already been cleared",
            status_code=409,
            code="already_cleared",
        )

    # 3. Insert manual 'out' event for the assignment's tunnel
    asgn = flag.assignment
    if not asgn:
        raise FlagClearError("Assignment associated with check flag not found", status_code=404)

    out_event = CheckEvent(
        user_id=flag.user_id,
        tunnel_id=asgn.tunnel_id,
        direction="out",
        method="manual",
        scanned_at=now,
        scanned_by=actor.id,
    )
    db.add(out_event)

    # 4. Stamp flag as cleared
    flag.cleared_at = now
    flag.cleared_by = actor.id
    flag.note = trimmed_note

    # 5. Audit record (detail: flag id only, NEVER note text)
    record_audit(
        db,
        actor,
        action="check_flag.clear",
        entity="check_flag",
        entity_id=flag.id,
        detail={"flag_id": flag.id},
    )

    db.commit()
    db.refresh(flag)
    return flag
