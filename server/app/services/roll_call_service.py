"""Incident roll call roster and personnel accounting service.

Owns roll call roster tracking, manual worker accounting, and gate scan roll call integration.
Must NOT handle HTTP requests, modify database schemas, or dispatch external notifications.
"""
import datetime
from typing import Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.incident_roster import IncidentRoster
from app.models.mine import Mine
from app.models.tunnel import Tunnel
from app.models.user import User
from app.services.audit import record_audit
from app.services import clock


class IncidentError(Exception):
    """Base exception for incident domain violations."""
    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def mark_accounted(
    db: Session,
    actor: Optional[User],
    incident: Incident,
    user_id: int,
    now: Optional[datetime.datetime] = None,
) -> IncidentRoster:
    """Marks a single worker as accounted for in the incident roll call.

    A6: Locks incident row FOR UPDATE; rejects closed incident with 409 incident_closed.
    """
    if now is None:
        now = clock.utcnow()

    # Lock incident row FOR UPDATE
    locked_inc = (
        db.query(Incident)
        .filter(Incident.id == incident.id)
        .with_for_update()
        .first()
    )
    if not locked_inc:
        raise IncidentError("Incident not found", status_code=404)

    if locked_inc.status == "closed":
        raise IncidentError(
            "Cannot account personnel on closed incident",
            status_code=409,
            code="incident_closed",
        )

    entry = (
        db.query(IncidentRoster)
        .filter(
            IncidentRoster.incident_id == locked_inc.id,
            IncidentRoster.user_id == user_id,
        )
        .first()
    )
    if not entry:
        raise IncidentError("Worker not found in incident roll call", status_code=404)

    if entry.accounted_at is None:
        entry.accounted_at = now
        entry.accounted_by = actor.id if actor else None
        db.flush()

        record_audit(
            db=db,
            user=actor,
            action="incident.account",
            entity="incident",
            entity_id=locked_inc.id,
            detail={"incident_id": locked_inc.id, "user_id": user_id},
            company_id=locked_inc.mine.company_id if locked_inc.mine else 0,
        )

    return entry


def mark_accounted_on_exit(
    db: Session,
    scanner: Optional[User],
    worker: User,
    tunnel: Tunnel,
    now: Optional[datetime.datetime] = None,
) -> int:
    """Marks a worker accounted in open incidents on gate OUT scan.

    A3: Accounts worker in EVERY open incident of the same company whose roster
    contains them, regardless of the tunnel of the scan. Writes no audit row.
    """
    if now is None:
        now = clock.utcnow()

    open_incidents = (
        db.query(Incident)
        .join(Mine, Incident.mine_id == Mine.id)
        .filter(
            Incident.status == "open",
            Mine.company_id == worker.company_id,
        )
        .all()
    )
    count = 0
    for inc in open_incidents:
        entry = (
            db.query(IncidentRoster)
            .filter(
                IncidentRoster.incident_id == inc.id,
                IncidentRoster.user_id == worker.id,
                IncidentRoster.accounted_at.is_(None),
            )
            .first()
        )
        if entry:
            entry.accounted_at = now
            entry.accounted_by = scanner.id if scanner else None
            db.flush()
            # A3: scanner path writes no audit row
            count += 1

    return count


def add_late_arrival(
    db: Session,
    worker: User,
    tunnel: Tunnel,
    now: Optional[datetime.datetime] = None,
) -> None:
    """Adds a worker to open incident rosters as unaccounted on gate IN scan."""
    if now is None:
        now = clock.utcnow()

    open_incidents = (
        db.query(Incident)
        .filter(
            Incident.status == "open",
            Incident.mine_id == tunnel.mine_id,
            or_(Incident.tunnel_id == tunnel.id, Incident.tunnel_id.is_(None)),
        )
        .all()
    )
    for inc in open_incidents:
        entry = (
            db.query(IncidentRoster)
            .filter(
                IncidentRoster.incident_id == inc.id,
                IncidentRoster.user_id == worker.id,
            )
            .first()
        )
        if not entry:
            db.add(
                IncidentRoster(
                    incident_id=inc.id,
                    user_id=worker.id,
                    added_at=now,
                    accounted_at=None,
                    accounted_by=None,
                )
            )
        elif entry.accounted_at is not None:
            # If they previously left and now scanned back in, they are unaccounted again
            entry.accounted_at = None
            entry.accounted_by = None
    db.flush()
