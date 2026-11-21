"""HTTP router for time-series telemetry reading history.

Owns the GET /api/history endpoint with eager relationship loading.
Must NOT handle data ingestion, update node positions, or send alerts.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.services.hazard_classifier import (
    LEVEL_COLORS,
    LEVEL_INT_TO_STR,
    LEVEL_NORMAL,
    get_hazard_details,
)
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["history"])


@router.get("/api/history")
def api_history(
    gateway: Optional[str] = None,
    node_id: Optional[int] = None,
    limit: int = Query(60, ge=1, le=5000),
    only_alerts: bool = False,
    db: Session = Depends(get_db),
):
    """Returns telemetry points ordered by time for Chart.js inspector."""
    q = (
        db.query(Reading)
        .options(joinedload(Reading.node).joinedload(Node.gateway))
        .join(Node)
        .join(Gateway)
    )

    if gateway:
        q = q.filter(Gateway.mac == norm_mac(gateway))
    if node_id is not None:
        q = q.filter(Node.node_no == node_id)
    if only_alerts:
        q = q.filter(Reading.level > 0)

    rows = q.order_by(Reading.id.desc()).limit(limit).all()

    out = []
    for r in reversed(rows):
        st = LEVEL_INT_TO_STR.get(r.level, "normal")
        ana = get_hazard_details(
            tilt_x=r.tilt_x,
            tilt_y=r.tilt_y,
            vib_rms=r.vib_rms,
            shock=r.shock,
            batt_v=r.batt_v,
            batt_pct=r.batt_pct,
        )
        gw_name = r.node.gateway.name if (r.node and r.node.gateway) else "Gateway"
        gw_mac = r.node.gateway.mac if (r.node and r.node.gateway) else "UNKNOWN"
        node_num = r.node.node_no if r.node else 0

        out.append(
            {
                "id": r.id,
                "ts": r.ts.timestamp(),
                "gateway": gw_name,
                "gateway_mac_address": gw_mac,
                "node_id": node_num,
                "counter": r.counter,
                "tilt_x": round(float(r.tilt_x), 3),
                "tilt_y": round(float(r.tilt_y), 3),
                "vibration_magnitude": round(float(r.vib_rms), 4),
                "vibration_detected": bool(r.shock),
                "battery_voltage": round(float(r.batt_v), 3),
                "battery_percentage": round(float(r.batt_pct), 1),
                "status": st,
                "score": ana["score"],
                "reasons": ana["reasons"],
                "color": LEVEL_COLORS.get(st, LEVEL_COLORS[LEVEL_NORMAL]),
                "low_battery": float(r.batt_pct) <= 20.0,
            }
        )

    return {"readings": out}
