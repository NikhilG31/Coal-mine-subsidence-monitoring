"""Watchdog offline detection cycle evaluation service.

Owns rule evaluation for gateway and node timeouts, alert creation, and auto-resolution.
Must NOT import FastAPI routing constructs, run background threads, or dispatch external notifications.
"""
import datetime
import logging
from typing import Dict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.alert import Alert
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.services.connectivity import gateway_is_online, node_is_online
from app.services.node_placement import is_placed

logger = logging.getLogger(__name__)


def run_watchdog_cycle(db: Session, now: datetime.datetime) -> Dict[str, int]:
    """Evaluates connectivity timeouts and synchronizes open/resolved alert states.

    Executes in a single database transaction and returns {"opened": int, "resolved": int}.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    opened = 0
    resolved = 0

    # 1. Active gateways only that have been seen at least once
    gateways = (
        db.query(Gateway)
        .options(
            joinedload(Gateway.mine),
            joinedload(Gateway.nodes).joinedload(Node.tunnel),
        )
        .filter(Gateway.active.is_(True), Gateway.last_seen_at.isnot(None))
        .all()
    )

    for gw in gateways:
        mine_name = gw.mine.name if gw.mine else f"Mine {gw.mine_id}"
        gw_eui = getattr(gw, "eui", None) or gw.mac
        company_id = gw.mine.company_id if gw.mine else None

        if not gateway_is_online(gw.last_seen_at, now):
            # 2.a Gateway is offline
            # Open gateway_offline alert if none open
            open_gw_alert = (
                db.query(Alert)
                .filter(
                    Alert.gateway_id == gw.id,
                    Alert.kind == "gateway_offline",
                    Alert.resolved_at.is_(None),
                )
                .first()
            )
            if not open_gw_alert:
                new_alert = Alert(
                    company_id=company_id,
                    mine_id=gw.mine_id,
                    gateway_id=gw.id,
                    node_id=None,
                    kind="gateway_offline",
                    title=f"Gateway {gw_eui} offline ({mine_name})",
                    opened_at=now,
                )
                try:
                    with db.begin_nested():
                        db.add(new_alert)
                        db.flush()
                    opened += 1
                except IntegrityError:
                    pass  # Concurrent runner already opened this alert

            # Auto-resolve any open node_offline alerts for this gateway
            open_node_alerts = (
                db.query(Alert)
                .filter(
                    Alert.gateway_id == gw.id,
                    Alert.kind == "node_offline",
                    Alert.resolved_at.is_(None),
                )
                .all()
            )
            for alert in open_node_alerts:
                alert.resolved_at = now
                resolved += 1

        else:
            # 2.b Gateway is online
            # If a gateway_offline alert is open, resolve it
            open_gw_alert = (
                db.query(Alert)
                .filter(
                    Alert.gateway_id == gw.id,
                    Alert.kind == "gateway_offline",
                    Alert.resolved_at.is_(None),
                )
                .first()
            )
            if open_gw_alert:
                open_gw_alert.resolved_at = now
                resolved += 1

            # Check each placed node belonging to this gateway
            for node in gw.nodes:
                if not is_placed(node):
                    continue

                tunnel_name = (
                    node.tunnel.name
                    if node.tunnel
                    else f"Tunnel {node.tunnel_id}"
                )

                if not node_is_online(node.last_seen_at, now):
                    open_node_alert = (
                        db.query(Alert)
                        .filter(
                            Alert.node_id == node.id,
                            Alert.kind == "node_offline",
                            Alert.resolved_at.is_(None),
                        )
                        .first()
                    )
                    if not open_node_alert:
                        new_node_alert = Alert(
                            company_id=company_id,
                            mine_id=gw.mine_id,
                            gateway_id=gw.id,
                            node_id=node.id,
                            kind="node_offline",
                            title=f"Node {node.id} offline ({mine_name} / {tunnel_name})",
                            opened_at=now,
                        )
                        try:
                            with db.begin_nested():
                                db.add(new_node_alert)
                                db.flush()
                            opened += 1
                        except IntegrityError:
                            pass
                else:
                    open_node_alert = (
                        db.query(Alert)
                        .filter(
                            Alert.node_id == node.id,
                            Alert.kind == "node_offline",
                            Alert.resolved_at.is_(None),
                        )
                        .first()
                    )
                    if open_node_alert:
                        open_node_alert.resolved_at = now
                        resolved += 1

    db.commit()
    return {"opened": opened, "resolved": resolved}
