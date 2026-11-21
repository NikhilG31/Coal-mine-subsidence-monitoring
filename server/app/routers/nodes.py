"""HTTP router for sensor node status and telemetry readings.

Owns the GET /api/nodes endpoint and query filtering.
Must NOT define model classes or calculate raw threshold mathematics.
"""
import time
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import OFFLINE_AFTER_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.services.node_serializer import serialize_node
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["nodes"])


@router.get("/api/nodes")
def api_nodes(gateway: Optional[str] = None, db: Session = Depends(get_db)):
    """Returns latest telemetry and placement info for all nodes."""
    now_ts = time.time()
    query = db.query(Node)
    if gateway:
        query = query.join(Gateway).filter(Gateway.mac == norm_mac(gateway))
    nodes = query.order_by(Node.gateway_id, Node.node_no).all()

    node_ids = [n.id for n in nodes]
    latest_map = {}
    if node_ids:
        latest_readings = (
            db.query(Reading)
            .distinct(Reading.node_id)
            .filter(Reading.node_id.in_(node_ids))
            .order_by(Reading.node_id, Reading.ts.desc())
            .all()
        )
        latest_map = {r.node_id: r for r in latest_readings}

    nodes_list = [
        serialize_node(n, latest_map.get(n.id), now_ts, offline_after_s=OFFLINE_AFTER_S)
        for n in nodes
    ]
    return {"now": now_ts, "nodes": nodes_list}
