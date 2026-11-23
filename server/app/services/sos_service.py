"""Hardware device SOS button ingestion and merge domain service.

Owns device SOS report creation, time-window merging, flood guard suppression, and automated SOS incident opening.
Must NOT handle HTTP requests, access request headers directly, or import FastAPI.
"""
import datetime
import logging
from typing import Optional, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import (
    SOS_INCIDENT_SIREN,
    SOS_MAX_NEW_PER_HOUR,
    SOS_MERGE_WINDOW_S,
)
from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.mine import Mine
from app.models.node import Node
from app.models.worker_report import WorkerReport
from app.services.audit import record_audit
from app.services import clock
from app.services.incident_service import open_incident
from app.services.node_placement import is_placed

logger = logging.getLogger(__name__)


def ensure_sos_incident(
    db: Session,
    node: Node,
    gateway: Gateway,
    report: WorkerReport,
    now: Optional[datetime.datetime] = None,
) -> Optional[Incident]:
    """Ensures an open SOS incident exists and is linked to the report for placed nodes.

    Idempotent: for a placed node, reuses the open SOS incident of that tunnel or opens one
    (source="sos", level=3, siren=SOS_INCIDENT_SIREN, immediate roster snapshot), and sets
    report.incident_id. Catches ONLY the unique-index conflict on uq_incidents_open_sos and
    reuses the open SOS incident. Any other exception is logged with logger.exception and re-raised.
    Unplaced nodes open no incident.
    """
    if now is None:
        now = clock.utcnow()

    node_placed = is_placed(node)
    tunnel_id = node.tunnel_id if (node_placed and node.tunnel_id) else None
    if not node_placed or not tunnel_id:
        return None

    # Check if an open SOS incident already exists for this tunnel
    existing_sos_inc = (
        db.query(Incident)
        .filter(
            Incident.tunnel_id == tunnel_id,
            Incident.source == "sos",
            Incident.status == "open",
        )
        .first()
    )

    if existing_sos_inc:
        if report.incident_id != existing_sos_inc.id:
            report.incident_id = existing_sos_inc.id
            db.flush()
        return existing_sos_inc

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
            return inc
    except IntegrityError as exc:
        err_msg = str(exc)
        orig_msg = str(getattr(exc, "orig", ""))
        if "uq_incidents_open_sos" in err_msg or "uq_incidents_open_sos" in orig_msg:
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
                db.flush()
                return conflicting_inc
        logger.exception("IntegrityError opening SOS incident for node %d: %s", node.id, exc)
        raise
    except Exception as exc:
        logger.exception("Failed to open SOS incident for node %d: %s", node.id, exc)
        raise


def handle_device_sos(
    db: Session,
    node: Node,
    gateway: Gateway,
    now: Optional[datetime.datetime] = None,
) -> Tuple[WorkerReport, bool]:
    """Processes a device SOS hardware signal for a node.

    Applies the merge window (unresolved reports only) and flood guard rules.
    Returns:
        (report, is_newly_created)
    """
    if now is None:
        now = clock.utcnow()

    # 1. Resolve company_id without fallback: raise if gateway's mine or company cannot be resolved
    mine = gateway.mine
    if not mine and gateway.mine_id:
        mine = db.query(Mine).filter(Mine.id == gateway.mine_id).first()
    if not mine or not mine.company_id:
        raise ValueError(f"Gateway {gateway.mac} mine or company cannot be resolved")
    company_id = mine.company_id

    # 2. Serialize concurrent SOS signals for this node via row lock
    db.query(Node.id).filter(Node.id == node.id).with_for_update().first()

    # 3. Query newest device-SOS report for this node
    newest = (
        db.query(WorkerReport)
        .filter(
            WorkerReport.node_id == node.id,
            WorkerReport.source == "device",
        )
        .order_by(WorkerReport.last_signal_at.desc(), WorkerReport.id.desc())
        .first()
    )

    is_unresolved = newest is not None and newest.status in ("open", "acknowledged")

    # 4. Merge rule: within SOS_MERGE_WINDOW_S (default 60s) of latest signal, only if newest is unresolved
    if is_unresolved and (now - newest.last_signal_at).total_seconds() <= float(SOS_MERGE_WINDOW_S):
        newest.repeat_count += 1
        newest.last_signal_at = now
        ensure_sos_incident(db, node, gateway, newest, now)
        db.flush()
        return newest, False

    # 5. Flood guard check: at most SOS_MAX_NEW_PER_HOUR (default 6) NEW reports per node per hour
    # Applies only when newest report is unresolved; a resolved newest report never absorbs a signal
    if is_unresolved:
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
                newest.id,
            )
            newest.repeat_count += 1
            newest.last_signal_at = now
            ensure_sos_incident(db, node, gateway, newest, now)
            db.flush()
            return newest, False

    # 6. Create NEW device-SOS report (newest is None, or newest is resolved, or under flood limit)
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

    # 7. Ensure open SOS incident for placed nodes
    ensure_sos_incident(db, node, gateway, report, now)
    db.flush()

    # 8. Record audit log
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
