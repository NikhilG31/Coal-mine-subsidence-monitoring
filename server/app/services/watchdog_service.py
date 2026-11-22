"""Watchdog offline detection cycle evaluation service.

Owns rule evaluation for gateway and node timeouts, alert creation, and auto-resolution.
Must NOT import FastAPI routing constructs, run background threads, or dispatch external notifications.
"""
import datetime
import logging
from collections import defaultdict
from typing import Dict, List

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.alert import Alert
from app.models.gateway import Gateway
from app.models.node import Node
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

    # 1. Resolve stale open alerts whose subject is no longer eligible:
    # (a) open gateway_offline or node_offline alerts of an inactive gateway (active=False)
    # (b) open node_offline alerts of a node that is no longer placed (is_placed false)
    # Executed in a single pass per cycle.
    stale_alerts = (
        db.query(Alert)
        .join(Gateway, Gateway.id == Alert.gateway_id)
        .outerjoin(Node, Node.id == Alert.node_id)
        .filter(
            Alert.resolved_at.is_(None),
            or_(
                Gateway.active.is_(False),
                (
                    (Alert.kind == "node_offline")
                    & (
                        Node.id.is_(None)
                        | Node.tunnel_id.is_(None)
                        | Node.lat.is_(None)
                        | Node.lon.is_(None)
                    )
                ),
            ),
        )
        .all()
    )
    for alert in stale_alerts:
        alert.resolved_at = now
        resolved += 1
        logger.info(
            "Resolved stale %s alert %d (gateway %d, node %s)",
            alert.kind,
            alert.id,
            alert.gateway_id,
            alert.node_id,
        )

    # 2. Query active gateways that have been seen at least once
    gateways = (
        db.query(Gateway)
        .options(
            joinedload(Gateway.mine),
            joinedload(Gateway.nodes).joinedload(Node.tunnel),
        )
        .filter(Gateway.active.is_(True), Gateway.last_seen_at.isnot(None))
        .all()
    )

    gw_ids = [gw.id for gw in gateways]

    # Load all open alerts for evaluated gateways in ONE query per cycle
    open_alerts: List[Alert] = []
    if gw_ids:
        open_alerts = (
            db.query(Alert)
            .filter(
                Alert.gateway_id.in_(gw_ids),
                Alert.resolved_at.is_(None),
            )
            .all()
        )

    open_gw_alerts: Dict[int, Alert] = {}
    open_node_alerts_by_node: Dict[int, Alert] = {}
    open_node_alerts_by_gw: Dict[int, List[Alert]] = defaultdict(list)

    for alert in open_alerts:
        if alert.kind == "gateway_offline":
            open_gw_alerts[alert.gateway_id] = alert
        elif alert.kind == "node_offline" and alert.node_id is not None:
            open_node_alerts_by_node[alert.node_id] = alert
            open_node_alerts_by_gw[alert.gateway_id].append(alert)

    for gw in gateways:
        mine_name = gw.mine.name if gw.mine else f"Mine {gw.mine_id}"
        company_id = gw.mine.company_id if gw.mine else None

        if not gateway_is_online(gw.last_seen_at, now):
            # Gateway is offline: open gateway_offline alert if not already open
            open_gw_alert = open_gw_alerts.get(gw.id)
            if not open_gw_alert or open_gw_alert.resolved_at is not None:
                new_alert = Alert(
                    company_id=company_id,
                    mine_id=gw.mine_id,
                    gateway_id=gw.id,
                    node_id=None,
                    kind="gateway_offline",
                    title=f"Gateway {gw.name} offline ({mine_name})",
                    opened_at=now,
                )
                try:
                    with db.begin_nested():
                        db.add(new_alert)
                        db.flush()
                    opened += 1
                    open_gw_alerts[gw.id] = new_alert
                    logger.info("Opened gateway_offline alert for gateway %d (%s) at %s", gw.id, gw.name, mine_name)
                except IntegrityError:
                    pass  # Concurrent runner already opened this alert

            # Auto-resolve any open node_offline alerts for this gateway
            for alert in open_node_alerts_by_gw.get(gw.id, []):
                if alert.resolved_at is None:
                    alert.resolved_at = now
                    resolved += 1
                    logger.info(
                        "Resolved node_offline alert %d for node %d (gateway %d offline)",
                        alert.id,
                        alert.node_id,
                        gw.id,
                    )

        else:
            # Gateway is online: resolve any open gateway_offline alert
            open_gw_alert = open_gw_alerts.get(gw.id)
            if open_gw_alert and open_gw_alert.resolved_at is None:
                open_gw_alert.resolved_at = now
                resolved += 1
                logger.info(
                    "Resolved gateway_offline alert %d for gateway %d (%s) - back online",
                    open_gw_alert.id,
                    gw.id,
                    gw.name,
                )

            # Check each placed node belonging to this gateway
            for node in gw.nodes:
                if not is_placed(node):
                    continue

                # Placed node with last_seen_at NULL never raises an alert
                if node.last_seen_at is None:
                    continue

                tunnel_name = (
                    node.tunnel.name
                    if node.tunnel
                    else f"Tunnel {node.tunnel_id}"
                )

                if not node_is_online(node.last_seen_at, now):
                    open_node_alert = open_node_alerts_by_node.get(node.id)
                    if not open_node_alert or open_node_alert.resolved_at is not None:
                        new_node_alert = Alert(
                            company_id=company_id,
                            mine_id=gw.mine_id,
                            gateway_id=gw.id,
                            node_id=node.id,
                            kind="node_offline",
                            title=f"Node {node.node_no} offline ({gw.name} / {mine_name} / {tunnel_name})",
                            opened_at=now,
                        )
                        try:
                            with db.begin_nested():
                                db.add(new_node_alert)
                                db.flush()
                            opened += 1
                            open_node_alerts_by_node[node.id] = new_node_alert
                            logger.info(
                                "Opened node_offline alert for node %d (number %d) at gateway %s",
                                node.id,
                                node.node_no,
                                gw.name,
                            )
                        except IntegrityError:
                            pass
                else:
                    open_node_alert = open_node_alerts_by_node.get(node.id)
                    if open_node_alert and open_node_alert.resolved_at is None:
                        open_node_alert.resolved_at = now
                        resolved += 1
                        logger.info(
                            "Resolved node_offline alert %d for node %d (number %d) - back online",
                            open_node_alert.id,
                            node.id,
                            node.node_no,
                        )

    db.commit()
    return {"opened": opened, "resolved": resolved}
