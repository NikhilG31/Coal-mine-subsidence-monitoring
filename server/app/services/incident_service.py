"""Emergency incident and roll call management domain service.

Owns incident lifecycle, roster snapshotting, gate scan accounting, and evacuation control.
Must NOT handle HTTP requests, import FastAPI components, or mutate raw sensor telemetry.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.incident_roster import IncidentRoster
from app.models.mine import Mine
from app.models.node import Node
from app.models.reading import Reading
from app.models.tunnel import Tunnel
from app.models.user import User
from app.services.audit import record_audit
from app.services import clock
from app.services.headcount import inside_now
from app.services.node_command_service import create_command
from app.services.node_placement import PLACED_SQL_EXPRESSION, is_placed

logger = logging.getLogger(__name__)


class IncidentError(Exception):
    """Base exception for incident domain violations."""
    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class IncidentConflictError(IncidentError):
    """Raised when closing an incident with unaccounted workers remaining."""
    def __init__(self, detail: str, status_code: int = 409, code: str = "unaccounted_workers", count: int = 0):
        super().__init__(detail=detail, status_code=status_code, code=code)
        self.count = count


def get_affected_nodes(
    db: Session,
    mine_id: int,
    tunnel_id: Optional[int] = None,
) -> List[Node]:
    """B0: Returns placed nodes of that tunnel or mine whose gateway is active."""
    query = (
        db.query(Node)
        .join(Gateway, Node.gateway_id == Gateway.id)
        .join(Tunnel, Node.tunnel_id == Tunnel.id)
        .filter(
            Gateway.active.is_(True),
            PLACED_SQL_EXPRESSION,
        )
    )
    if tunnel_id is not None:
        query = query.filter(Node.tunnel_id == tunnel_id)
    else:
        query = query.filter(Tunnel.mine_id == mine_id)

    return query.all()


def open_incident(
    db: Session,
    actor: Optional[User],
    mine_id: int,
    tunnel_id: Optional[int],
    title: str,
    level: int,
    siren: bool,
    source: str = "manual",
    now: Optional[datetime.datetime] = None,
) -> Incident:
    """Opens a new emergency or manual evacuation incident.

    Snapshots the inside worker roster, sets siren state, creates buzzer commands, and logs audit.
    """
    if now is None:
        now = clock.utcnow()

    mine = db.query(Mine).filter(Mine.id == mine_id).first()
    if not mine:
        raise IncidentError("Mine not found", status_code=404)

    target_tunnel_ids: List[int] = []
    if tunnel_id is not None:
        tunnel = db.query(Tunnel).filter(Tunnel.id == tunnel_id, Tunnel.mine_id == mine_id).first()
        if not tunnel:
            raise IncidentError("Tunnel not found in specified mine", status_code=404)
        target_tunnel_ids = [tunnel_id]
    else:
        all_tunnels = db.query(Tunnel.id).filter(Tunnel.mine_id == mine_id).all()
        target_tunnel_ids = [t[0] for t in all_tunnels]

    incident = Incident(
        mine_id=mine_id,
        tunnel_id=tunnel_id,
        source=source,
        level=level,
        title=title,
        status="open",
        siren_active=siren,
        opened_by=actor.id if actor else None,
        opened_at=now,
    )
    db.add(incident)
    db.flush()

    # 1. Dispatch buzzer_on to affected nodes if siren is requested
    if siren:
        affected = get_affected_nodes(db, mine_id, tunnel_id)
        for node in affected:
            create_command(db, actor, node, "buzzer_on", now=now)

    # 2. Snapshot headcount roster
    worker_ids = set()
    if target_tunnel_ids:
        inside_dict = inside_now(db, target_tunnel_ids)
        for tid, workers in inside_dict.items():
            for w in workers:
                worker_ids.add(w["id"])

    for uid in worker_ids:
        db.add(
            IncidentRoster(
                incident_id=incident.id,
                user_id=uid,
                added_at=now,
                accounted_at=None,
                accounted_by=None,
            )
        )
    db.flush()

    # 3. Audit incident.open (no title, worker name or PII in detail)
    record_audit(
        db=db,
        user=actor,
        action="incident.open",
        entity="incident",
        entity_id=incident.id,
        detail={
            "incident_id": incident.id,
            "mine_id": mine_id,
            "tunnel_id": tunnel_id,
            "level": level,
            "siren": siren,
            "roster_count": len(worker_ids),
        },
        company_id=mine.company_id,
    )

    return incident


def auto_open_for_critical(
    db: Session,
    node: Node,
    reading: Reading,
    prev_level: int,
    now: Optional[datetime.datetime] = None,
) -> Optional[Incident]:
    """Idempotently opens an auto incident when a placed node reaches CRITICAL (level 3).

    B3: NEVER fails ingest. Wraps in savepoint and catches duplicate partial unique index race.
    """
    if not is_placed(node) or node.tunnel_id is None:
        return None
    if reading.level != 3 or prev_level >= 3:
        return None

    if now is None:
        now = clock.utcnow()

    tunnel = db.query(Tunnel).options(joinedload(Tunnel.mine)).filter(Tunnel.id == node.tunnel_id).first()
    if not tunnel or not tunnel.mine:
        return None

    try:
        # Wrap in savepoint so any conflict or failure is fully isolated from ingest transaction
        with db.begin_nested():
            existing = (
                db.query(Incident)
                .filter(
                    Incident.tunnel_id == node.tunnel_id,
                    Incident.status == "open",
                    Incident.source == "auto",
                )
                .first()
            )
            if existing:
                return existing

            title = f"CRITICAL: Tilt/vibration on node {node.node_no} ({tunnel.name})"
            inc = open_incident(
                db=db,
                actor=None,
                mine_id=tunnel.mine_id,
                tunnel_id=node.tunnel_id,
                title=title,
                level=3,
                siren=True,
                source="auto",
                now=now,
            )
            return inc
    except IntegrityError:
        logger.info("Auto-incident duplicate race suppressed for tunnel %d", node.tunnel_id)
        return None
    except Exception as exc:
        logger.error("Failed to auto-open critical incident: %s", exc)
        return None


def mark_accounted(
    db: Session,
    actor: Optional[User],
    incident: Incident,
    user_id: int,
    now: Optional[datetime.datetime] = None,
) -> IncidentRoster:
    """Marks a single worker as accounted for in the incident roll call."""
    if now is None:
        now = clock.utcnow()

    entry = (
        db.query(IncidentRoster)
        .filter(
            IncidentRoster.incident_id == incident.id,
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
            entity_id=incident.id,
            detail={"incident_id": incident.id, "user_id": user_id},
            company_id=incident.mine.company_id if incident.mine else 0,
        )

    return entry


def mark_accounted_on_exit(
    db: Session,
    scanner: Optional[User],
    worker: User,
    tunnel: Tunnel,
    now: Optional[datetime.datetime] = None,
) -> int:
    """Marks a worker accounted in open incidents on gate OUT scan."""
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

            record_audit(
                db=db,
                user=scanner,
                action="incident.account",
                entity="incident",
                entity_id=inc.id,
                detail={"incident_id": inc.id, "user_id": worker.id},
                company_id=tunnel.mine.company_id if tunnel.mine else 0,
            )
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


def close_incident(
    db: Session,
    actor: Optional[User],
    incident: Incident,
    close_note: Optional[str],
    confirm_unaccounted: bool = False,
    now: Optional[datetime.datetime] = None,
) -> Incident:
    """Closes an open incident, turning off sirens and issuing buzzer_off commands."""
    if incident.status == "closed":
        raise IncidentError("Incident is already closed", status_code=409, code="already_closed")

    if now is None:
        now = clock.utcnow()

    unaccounted_count = (
        db.query(IncidentRoster)
        .filter(
            IncidentRoster.incident_id == incident.id,
            IncidentRoster.accounted_at.is_(None),
        )
        .count()
    )

    if unaccounted_count > 0 and not confirm_unaccounted:
        raise IncidentConflictError(
            f"Cannot close incident: {unaccounted_count} personnel remain unaccounted for",
            status_code=409,
            code="unaccounted_workers",
            count=unaccounted_count,
        )

    incident.status = "closed"
    incident.closed_at = now
    incident.closed_by = actor.id if actor else None
    incident.close_note = close_note
    incident.siren_active = False

    # Issue buzzer_off commands to all affected nodes
    affected = get_affected_nodes(db, incident.mine_id, incident.tunnel_id)
    for node in affected:
        create_command(db, actor, node, "buzzer_off", now=now)

    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="incident.close",
        entity="incident",
        entity_id=incident.id,
        detail={
            "incident_id": incident.id,
            "unaccounted_count": unaccounted_count,
            "confirmed": bool(confirm_unaccounted),
        },
        company_id=incident.mine.company_id if incident.mine else 0,
    )

    return incident


def set_siren(
    db: Session,
    actor: Optional[User],
    incident: Incident,
    on: bool,
    now: Optional[datetime.datetime] = None,
) -> Incident:
    """Toggles siren state for an open incident and creates buzzer commands."""
    if incident.status == "closed":
        raise IncidentError("Cannot change siren on closed incident", status_code=409, code="incident_closed")

    if now is None:
        now = clock.utcnow()

    incident.siren_active = on
    cmd = "buzzer_on" if on else "buzzer_off"
    affected = get_affected_nodes(db, incident.mine_id, incident.tunnel_id)
    for node in affected:
        create_command(db, actor, node, cmd, now=now)

    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="incident.siren",
        entity="incident",
        entity_id=incident.id,
        detail={"incident_id": incident.id, "on": on},
        company_id=incident.mine.company_id if incident.mine else 0,
    )

    return incident
