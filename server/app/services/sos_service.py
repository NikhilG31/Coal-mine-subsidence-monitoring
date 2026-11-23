"""Hardware device SOS button ingestion and merge domain service.

Owns device SOS report creation, time-window merging, flood guard suppression, and automated SOS incident opening.
Must NOT handle HTTP requests, access request headers directly, or import FastAPI.
"""
import datetime
import logging
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.config import (
    SOS_INCIDENT_SIREN,
    SOS_MAX_NEW_PER_HOUR,
    SOS_MERGE_WINDOW_S,
)
from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.node import Node
from app.models.worker_report import WorkerReport
from app.services.audit import record_audit
from app.services import clock
from app.services.incident_service import open_incident
from app.services.node_placement import is_placed

logger = logging.getLogger(__name__)


def handle_device_sos(
    db: Session,
    node: Node,
    gateway: Gateway,
    now: Optional[datetime.datetime] = None,
) -> Tuple[WorkerReport, bool]:
    """Processes a device SOS hardware signal for a node.

    Applies the 60s merge window and flood guard rules.
    Returns:
        (report, is_newly_created)
    """
    if now is None:
        now = clock.utcnow()

    # 1. Serialize concurrent SOS signals for this node via row lock
    db.query(Node.id).filter(Node.id == node.id).with_for_update().first()

    # 2. Query newest device-SOS report for this node
    newest = (
        db.query(WorkerReport)
        .filter(
            WorkerReport.node_id == node.id,
            WorkerReport.source == "device",
        )
        .order_by(WorkerReport.last_signal_at.desc(), WorkerReport.id.desc())
        .first()
    )

    # 3. Merge rule: within SOS_MERGE_WINDOW_S (default 60s) of latest signal
    if newest and (now - newest.last_signal_at).total_seconds() <= float(SOS_MERGE_WINDOW_S):
        newest.repeat_count += 1
        newest.last_signal_at = now
        db.flush()
        return newest, False

    # 4. Flood guard check: at most SOS_MAX_NEW_PER_HOUR (default 6) NEW reports per node per hour
    hour_cutoff = now - datetime.timedelta(hours=1)
    new_reports_count = (
        db.query(WorkerReport)
        .filter(
            WorkerReport.node_id == node.id,
            WorkerReport.source == "device",
            WorkerReport.created_at >= hour_cutoff,
        )
        .count()
    )

    if new_reports_count >= SOS_MAX_NEW_PER_HOUR:
        logger.warning(
            "SOS flood guard triggered for node %d (gateway %s): %d reports in last hour. Merging into report %d.",
            node.id,
            gateway.mac,
            new_reports_count,
            newest.id if newest else 0,
        )
        if newest:
            newest.repeat_count += 1
            newest.last_signal_at = now
            db.flush()
            return newest, False

    # 5. Create NEW device-SOS report
    node_placed = is_placed(node)
    tunnel_id = node.tunnel_id if (node_placed and node.tunnel_id) else None

    if node_placed and node.tunnel:
        desc = f"Device SOS from node {node.node_no} ({node.tunnel.name})"
    else:
        desc = f"Device SOS from unplaced node {node.node_no} (gateway {gateway.name})"

    report = WorkerReport(
        mine_id=gateway.mine_id,
        tunnel_id=tunnel_id,
        user_id=None,
        node_id=node.id,
        source="device",
        type="sos",
        severity="critical",
        description=desc,
        status="open",
        created_at=now,
        last_signal_at=now,
        repeat_count=1,
    )
    db.add(report)
    db.flush()

    # 6. For placed nodes, open or link emergency SOS incident with immediate roster snapshot
    if node_placed and tunnel_id:
        existing_sos_inc = (
            db.query(Incident)
            .filter(
                Incident.tunnel_id == tunnel_id,
                Incident.source == "sos",
                Incident.status == "open",
            )
            .first()
        )

        if not existing_sos_inc:
            try:
                with db.begin_nested():
                    tunnel_name = node.tunnel.name if node.tunnel else f"Tunnel {tunnel_id}"
                    inc = open_incident(
                        db=db,
                        actor=None,
                        mine_id=gateway.mine_id,
                        tunnel_id=tunnel_id,
                        title=f"SOS: node {node.node_no} ({tunnel_name})",
                        level=3,
                        siren=SOS_INCIDENT_SIREN,
                        source="sos",
                        now=now,
                    )
                    db.flush()
                    report.incident_id = inc.id
            except Exception:
                # Concurrent SOS incident opening conflict handled by reusing open incident
                conflicting_inc = (
                    db.query(Incident)
                    .filter(
                        Incident.tunnel_id == tunnel_id,
                        Incident.source == "sos",
                        Incident.status == "open",
                    )
                    .first()
                )
                if conflicting_inc:
                    report.incident_id = conflicting_inc.id
        else:
            report.incident_id = existing_sos_inc.id

        db.flush()

    # 7. Record audit log
    company_id = gateway.mine.company_id if (gateway.mine and gateway.mine.company_id) else 0
    record_audit(
        db=db,
        user=None,
        action="sos.device",
        entity="report",
        entity_id=report.id,
        detail={
            "report_id": report.id,
            "node_id": node.id,
            "tunnel_id": tunnel_id,
        },
        company_id=company_id,
    )

    return report, True
