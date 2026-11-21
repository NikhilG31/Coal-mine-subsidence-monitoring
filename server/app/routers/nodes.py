"""HTTP router for sensor node status and telemetry readings.

Owns the GET /api/nodes endpoint, tenant mine scoping, and role-based view models.
Must NOT define model classes or calculate raw threshold mathematics.
"""
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import OFFLINE_AFTER_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.models.user import User
from app.schemas.nodes import NodesResponse, SuperiorNodeView, WorkerNodeView
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
)
from app.security.roles import WORKER_ROLES
from app.services.node_serializer import (
    serialize_node,
    serialize_worker_node,
)
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["nodes"])


@router.get("/api/nodes", response_model=NodesResponse)
def api_nodes(
    gateway: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns latest telemetry and placement info scoped to permitted mines.

    Workers (foreman, miner) receive the stripped WorkerNodeView model.
    Superiors receive full diagnostic properties.
    """
    now_ts = time.time()
    allowed_mines = get_allowed_mine_ids(db, user)

    # Scoping: query nodes whose gateway belongs to user's allowed mines
    query = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(Gateway.mine_id.in_(allowed_mines))
    )

    if gateway:
        clean_mac = norm_mac(gateway)
        # Check if gateway exists at all
        gw_record = db.query(Gateway).filter(Gateway.mac == clean_mac).first()
        if not gw_record or gw_record.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Gateway not found")
        query = query.filter(Gateway.mac == clean_mac)

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

    is_worker = user.role in WORKER_ROLES

    nodes_list = []
    for n in nodes:
        reading = latest_map.get(n.id)
        if is_worker:
            data = serialize_worker_node(n, reading, now_ts, offline_after_s=OFFLINE_AFTER_S)
            nodes_list.append(WorkerNodeView.model_validate(data))
        else:
            data = serialize_node(n, reading, now_ts, offline_after_s=OFFLINE_AFTER_S)
            nodes_list.append(SuperiorNodeView.model_validate(data))

    return {"now": now_ts, "nodes": nodes_list}
