"""Automated siren refresh daemon service.

Owns periodic buzzer command renewal to keep node hardware buzzers active past timeout.
Must NOT handle HTTP requests, modify incident rosters, or bypass node row locks.
"""
import datetime
from typing import Dict, Optional
from sqlalchemy.orm import Session

from app.config import SIREN_REFRESH_S
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

    B3: For each affected node, if newest buzzer_on was created > SIREN_REFRESH_S ago, dispatches new command.
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

    cutoff = now - datetime.timedelta(seconds=SIREN_REFRESH_S)
    refreshed_count = 0

    # Collect affected nodes across all open siren incidents
    affected_nodes_map: Dict[int, Node] = {}
    for inc in open_sirens:
        nodes = get_affected_nodes(db, inc.mine_id, inc.tunnel_id)
        for n in nodes:
            affected_nodes_map[n.id] = n

    for node_id, node in affected_nodes_map.items():
        newest_on = (
            db.query(NodeCommand)
            .filter(
                NodeCommand.node_id == node_id,
                NodeCommand.cmd == "buzzer_on",
            )
            .order_by(NodeCommand.created_at.desc(), NodeCommand.id.desc())
            .first()
        )
        if newest_on is None or newest_on.created_at <= cutoff:
            create_command(db, actor=None, node=node, cmd="buzzer_on", now=now)
            refreshed_count += 1

    return refreshed_count
