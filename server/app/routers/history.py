"""HTTP router for time-series telemetry reading history.

Owns the GET /api/history endpoint with eager relationship loading, superior authorization, and mine scoping.
Must NOT handle data ingestion, update node positions, or send alerts.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.models.user import User
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
)
from app.security.roles import SUPERIOR_ROLES
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
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns telemetry points ordered by time for Chart.js inspector (superiors only, mine scoped)."""
    allowed_mines = get_allowed_mine_ids(db, user)

    q = (
        db.query(Reading)
        .options(joinedload(Reading.node).joinedload(Node.gateway))
        .join(Node)
        .join(Gateway)
        .filter(Gateway.mine_id.in_(allowed_mines))
    )

    if gateway:
        clean_mac = norm_mac(gateway)
        gw_record = db.query(Gateway).filter(Gateway.mac == clean_mac).first()
        if not gw_record or gw_record.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Gateway not found")
        q = q.filter(Gateway.mac == clean_mac)

    if node_id is not None:
        if gateway:
            node_exists = (
                db.query(Node)
                .join(Gateway)
                .filter(Gateway.mac == norm_mac(gateway), Node.node_no == node_id, Gateway.mine_id.in_(allowed_mines))
                .first()
            )
            if not node_exists:
                raise HTTPException(status_code=404, detail="Node not found")
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
