"""Emergency incident lifecycle and evacuation management domain service.

Owns incident opening, closing, siren toggling, and auto-incident generation.
Must NOT handle HTTP requests, import FastAPI components, or mutate raw sensor telemetry.
"""
import datetime
import logging
from typing import List, Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import AUTO_INCIDENT_SIREN
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
from app.services.roll_call_service import (
    IncidentError,
    add_late_arrival,
    mark_accounted,
    mark_accounted_on_exit,
)

__all__ = [
    "IncidentConflictError",
    "IncidentError",
    "add_late_arrival",
    "mark_accounted",
    "mark_accounted_on_exit",
    "get_affected_nodes",
    "open_incident",
    "auto_open_for_critical",
    "close_incident",
    "set_siren",
]

logger = logging.getLogger(__name__)


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
    """Returns placed nodes of that tunnel or mine whose gateway is active."""
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
    """Opens a new emergency or manual evacuation incident and snapshots inside roster."""
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

    # 3. Audit incident.open
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

    A4 & A5: Uses AUTO_INCIDENT_SIREN config and logs tracebacks on unexpected errors.
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
                siren=AUTO_INCIDENT_SIREN,
                source="auto",
                now=now,
            )
            return inc
    except IntegrityError:
        logger.info("Auto-incident duplicate race suppressed for tunnel %d", node.tunnel_id)
        return None
    except Exception as exc:
        logger.error("Failed to auto-open critical incident: %s", exc, exc_info=True)
        return None


def close_incident(
    db: Session,
    actor: Optional[User],
    incident: Incident,
    close_note: Optional[str],
    confirm_unaccounted: bool = False,
    now: Optional[datetime.datetime] = None,
) -> Incident:
    """Closes an open incident, turning off sirens and issuing buzzer_off commands.

    A6: Locks incident row FOR UPDATE.
    """
    locked_inc = (
        db.query(Incident)
        .filter(Incident.id == incident.id)
        .with_for_update()
        .first()
    )
    if not locked_inc:
        raise IncidentError("Incident not found", status_code=404)

    if locked_inc.status == "closed":
        raise IncidentError("Incident is already closed", status_code=409, code="already_closed")

    if now is None:
        now = clock.utcnow()

    unaccounted_count = (
        db.query(IncidentRoster)
        .filter(
            IncidentRoster.incident_id == locked_inc.id,
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

    locked_inc.status = "closed"
    locked_inc.closed_at = now
    locked_inc.closed_by = actor.id if actor else None
    locked_inc.close_note = close_note
    locked_inc.siren_active = False

    # Issue buzzer_off commands to all affected nodes
    affected = get_affected_nodes(db, locked_inc.mine_id, locked_inc.tunnel_id)
    for node in affected:
        create_command(db, actor, node, "buzzer_off", now=now)

    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="incident.close",
        entity="incident",
        entity_id=locked_inc.id,
        detail={
            "incident_id": locked_inc.id,
            "unaccounted_count": unaccounted_count,
            "confirmed": bool(confirm_unaccounted),
        },
        company_id=locked_inc.mine.company_id if locked_inc.mine else 0,
    )

    return locked_inc


def set_siren(
    db: Session,
    actor: Optional[User],
    incident: Incident,
    on: bool,
    now: Optional[datetime.datetime] = None,
) -> Incident:
    """Toggles siren state for an open incident and creates buzzer commands.

    A6: Locks incident row FOR UPDATE.
    """
    locked_inc = (
        db.query(Incident)
        .filter(Incident.id == incident.id)
        .with_for_update()
        .first()
    )
    if not locked_inc:
        raise IncidentError("Incident not found", status_code=404)

    if locked_inc.status == "closed":
        raise IncidentError("Cannot change siren on closed incident", status_code=409, code="incident_closed")

    if now is None:
        now = clock.utcnow()

    locked_inc.siren_active = on
    cmd = "buzzer_on" if on else "buzzer_off"
    affected = get_affected_nodes(db, locked_inc.mine_id, locked_inc.tunnel_id)
    for node in affected:
        create_command(db, actor, node, cmd, now=now)

    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="incident.siren",
        entity="incident",
        entity_id=locked_inc.id,
        detail={"incident_id": locked_inc.id, "on": on},
        company_id=locked_inc.mine.company_id if locked_inc.mine else 0,
    )

    return locked_inc
