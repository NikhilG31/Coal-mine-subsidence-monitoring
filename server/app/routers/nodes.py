"""HTTP router for sensor node status and telemetry readings.

Owns the GET /api/nodes endpoint, tenant mine scoping, and role-based view models.
Must NOT define model classes or calculate raw threshold mathematics.
"""
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.user import User
from app.schemas.nodes import NodesResponse, SuperiorNodeView, WorkerNodeView
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    get_visible_tunnel_ids,
)
from app.security.roles import WORKER_ROLES
from app.services.latest_readings import get_latest_readings
from app.services.node_serializer import (
    serialize_node,
    serialize_worker_node,
)
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["nodes"])


@router.get("/api/nodes", response_model=NodesResponse)
def api_nodes(
    gateway: Optional[str] = None,
    mine_id: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns latest telemetry and placement info scoped to permitted mines.

    Workers (foreman, miner) receive the stripped WorkerNodeView model.
    Superiors receive full diagnostic properties.
    """
    now_ts = time.time()
    allowed_mines = get_allowed_mine_ids(db, user)

    if mine_id is not None:
        if mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Mine not found")
        scoped_mines = [mine_id]
    else:
        scoped_mines = allowed_mines

    # Scoping: query nodes whose gateway belongs to user's allowed mines
    query = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(Gateway.mine_id.in_(scoped_mines))
    )

    # Step 5: Miner sees ONLY nodes placed in their visible tunnels (none -> empty list)
    visible_tunnels = get_visible_tunnel_ids(db, user)
    if visible_tunnels is not None:
        if not visible_tunnels:
            return {"now": now_ts, "nodes": []}
        query = query.filter(Node.tunnel_id.in_(visible_tunnels))

    if gateway:
        clean_mac = norm_mac(gateway)
        # Check if gateway exists at all
        gw_record = db.query(Gateway).filter(Gateway.mac == clean_mac).first()
        if not gw_record or gw_record.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Gateway not found")
        query = query.filter(Gateway.mac == clean_mac)

    nodes = query.order_by(Node.gateway_id, Node.node_no).all()

    node_ids = [n.id for n in nodes]
    latest_map = get_latest_readings(db, node_ids)

    is_worker = user.role in WORKER_ROLES

    nodes_list = []
    for n in nodes:
        reading = latest_map.get(n.id)
        if is_worker:
            data = serialize_worker_node(n, reading, now_ts)
            nodes_list.append(WorkerNodeView.model_validate(data))
        else:
            data = serialize_node(n, reading, now_ts)
            nodes_list.append(SuperiorNodeView.model_validate(data))

    return {"now": now_ts, "nodes": nodes_list}
