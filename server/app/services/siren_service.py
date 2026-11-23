"""Automated siren refresh daemon service.

Owns periodic buzzer command renewal to keep node hardware buzzers active past timeout.
Must NOT handle HTTP requests, modify incident rosters, or commit transactions.
"""
import datetime
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.config import COMMAND_TTL_S, SIREN_REFRESH_S
from app.models.audit_log import AuditLog
from app.models.incident import Incident
from app.models.node import Node
from app.models.node_command import NodeCommand
from app.services import clock
from app.services.incident_service import get_affected_nodes
from app.services.node_command_service import create_command


def run_siren_cycle(
    db: Session,
    now: Optional[datetime.datetime] = None,
) -> int:
    """Refreshes buzzer_on commands for active siren incidents to prevent hardware auto-silence.

    A2: For each affected node:
    - If newest buzzer_on is still PENDING (unacked, not expired): extend its expires_at instead of creating a new row.
    - If it was ACKED and older than SIREN_REFRESH_S: create a new buzzer_on (new seq).
    - Skip a node whose newest command is a buzzer_off created AFTER the incident's last siren-on event.
    - Never commits; the caller (runner) commits and rolls back on error.
    """
    if now is None:
        now = clock.utcnow()

    open_sirens = (
        db.query(Incident)
        .filter(
            Incident.status == "open",
            Incident.siren_active.is_(True),
        )
        .all()
    )
    if not open_sirens:
        return 0

    inc_ids = [inc.id for inc in open_sirens]

    # Map each incident to its latest siren-on event timestamp
    last_siren_on_map: Dict[int, datetime.datetime] = {inc.id: inc.opened_at for inc in open_sirens}
    siren_audits = (
        db.query(AuditLog.entity_id, AuditLog.ts, AuditLog.detail)
        .filter(
            AuditLog.entity == "incident",
            AuditLog.entity_id.in_(inc_ids),
            AuditLog.action.in_(("incident.siren", "incident.open")),
        )
        .order_by(AuditLog.ts.asc())
        .all()
    )
    for ent_id, ts, detail in siren_audits:
        if detail and (detail.get("siren") is True or detail.get("on") is True):
            last_siren_on_map[ent_id] = max(last_siren_on_map.get(ent_id, ts), ts)

    # Collect affected nodes and which open siren incidents affect them
    node_incidents_map: Dict[int, List[Incident]] = {}
    affected_nodes_map: Dict[int, Node] = {}
    for inc in open_sirens:
        nodes = get_affected_nodes(db, inc.mine_id, inc.tunnel_id)
        for n in nodes:
            affected_nodes_map[n.id] = n
            if n.id not in node_incidents_map:
                node_incidents_map[n.id] = []
            node_incidents_map[n.id].append(inc)

    refreshed_count = 0
    ttl_delta = datetime.timedelta(seconds=COMMAND_TTL_S)

    for node_id, node in affected_nodes_map.items():
        # Latest siren-on event among all incidents affecting this node
        affecting = node_incidents_map.get(node_id, [])
        if not affecting:
            continue
        node_last_siren_on = max(last_siren_on_map[inc.id] for inc in affecting)

        # Newest command for this node (any type)
        newest_cmd = (
            db.query(NodeCommand)
            .filter(NodeCommand.node_id == node_id)
            .order_by(NodeCommand.created_at.desc(), NodeCommand.id.desc())
            .first()
        )
        # Skip node if a superior purposefully silenced it with buzzer_off after last siren-on
        if newest_cmd and newest_cmd.cmd == "buzzer_off" and newest_cmd.created_at > node_last_siren_on:
            continue

        # Newest buzzer_on command
        newest_on = (
            db.query(NodeCommand)
            .filter(
                NodeCommand.node_id == node_id,
                NodeCommand.cmd == "buzzer_on",
            )
            .order_by(NodeCommand.created_at.desc(), NodeCommand.id.desc())
            .first()
        )

        if newest_on is not None and newest_on.acked_at is None and newest_on.expires_at > now:
            # Still PENDING: extend expires_at instead of creating a new row
            newest_on.expires_at = now + ttl_delta
            db.flush()
            refreshed_count += 1
        elif newest_on is not None and newest_on.acked_at is not None:
            # Was ACKED: if older than SIREN_REFRESH_S, create a new buzzer_on
            if (now - newest_on.created_at).total_seconds() >= SIREN_REFRESH_S:
                create_command(db, actor=None, node=node, cmd="buzzer_on", now=now)
                refreshed_count += 1
        elif newest_on is None or newest_on.expires_at <= now:
            # No prior command or expired
            create_command(db, actor=None, node=node, cmd="buzzer_on", now=now)
            refreshed_count += 1

    return refreshed_count
