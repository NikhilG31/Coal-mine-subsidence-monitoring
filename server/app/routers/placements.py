"""HTTP router for node geospatial placement and unplaced queue management.

Owns GET /api/placements, GET /api/unplaced, and PATCH/DELETE /api/nodes/{id}/placement.
Enforces superior authorization, tenant mine scoping, and placement completeness.
"""
import datetime
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.models.user import User
from app.schemas.placements import (
    PlacementsResponse,
    SuperiorPlacementView,
    WorkerPlacementView,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
    security_logger,
)
from app.security.roles import SUPERIOR_ROLES, WORKER_ROLES
from app.services.audit import record_audit
from app.services.latest_readings import get_latest_readings
from app.services.node_placement import (
    PLACED_SQL_EXPRESSION,
    UNPLACED_SQL_EXPRESSION,
)
from app.services.node_serializer import serialize_node

router = APIRouter(tags=["placements"])


class NodePlacementRequest(BaseModel):
    tunnel_id: int
    lat: float
    lon: float

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v: float) -> float:
        if not (-90.0 <= v <= 90.0):
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, v: float) -> float:
        if not (-180.0 <= v <= 180.0):
            raise ValueError("Longitude must be between -180 and 180")
        return v


@router.get("/api/placements", response_model=PlacementsResponse)
def api_placements(
    mine_id: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns placed nodes and their coordinates scoped to permitted mines (any role)."""
    allowed_mines = get_allowed_mine_ids(db, user)

    if mine_id is not None:
        if mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Mine not found")
        scoped_mines = [mine_id]
    else:
        scoped_mines = allowed_mines

    placed = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(
            Gateway.mine_id.in_(scoped_mines),
            PLACED_SQL_EXPRESSION,
        )
        .all()
    )
    is_worker = user.role in WORKER_ROLES
    res = []
    for n in placed:
        mac = n.gateway.mac if n.gateway else "UNKNOWN"
        loc = n.tunnel.name if n.tunnel else f"Node {n.node_no}"
        placed_ts = n.placed_at.timestamp() if n.placed_at else None
        if is_worker:
            res.append(
                WorkerPlacementView(
                    node_id=n.node_no,
                    location_name=loc,
                    latitude=n.lat,
                    longitude=n.lon,
                    placed_at=placed_ts,
                )
            )
        else:
            res.append(
                SuperiorPlacementView(
                    gateway_mac_address=mac,
                    node_id=n.node_no,
                    location_name=loc,
                    latitude=n.lat,
                    longitude=n.lon,
                    placed_at=placed_ts,
                    placed_by=n.placed_by,
                )
            )
    return {"placements": res}


@router.get("/api/unplaced")
def api_unplaced(
    mine_id: Optional[int] = None,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns unplaced nodes reporting data in permitted mines (superiors only)."""
    now_ts = time.time()
    allowed_mines = get_allowed_mine_ids(db, user)

    if mine_id is not None:
        if mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Mine not found")
        scoped_mines = [mine_id]
    else:
        scoped_mines = allowed_mines

    unplaced_nodes = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(
            Gateway.mine_id.in_(scoped_mines),
            UNPLACED_SQL_EXPRESSION,
        )
        .all()
    )
    node_ids = [n.id for n in unplaced_nodes]
    latest_map = get_latest_readings(db, node_ids)

    serialized = [
        serialize_node(n, latest_map.get(n.id), now_ts)
        for n in unplaced_nodes
    ]
    return {"unplaced": serialized}


@router.patch("/api/nodes/{node_id}/placement")
def api_place_node(
    node_id: int,
    req: NodePlacementRequest,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Assigns tunnel and geospatial coordinates to a node (superiors only, mine scoped)."""
    allowed_mines = get_allowed_mine_ids(db, user)

    node = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(Node.id == node_id, Gateway.mine_id.in_(allowed_mines))
        .first()
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    tunnel = db.query(Tunnel).filter(Tunnel.id == req.tunnel_id).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")

    # Placement rule: tunnel must belong to the same mine as the node's gateway
    if tunnel.mine_id != node.gateway.mine_id:
        raise HTTPException(
            status_code=400,
            detail="Tunnel does not belong to the same mine as the node's gateway",
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    node.tunnel_id = req.tunnel_id
    node.lat = req.lat
    node.lon = req.lon
    node.placed_by = user.id
    node.placed_at = now

    record_audit(
        db,
        user,
        action="node.place",
        entity="node",
        entity_id=node.id,
        detail={"tunnel_id": req.tunnel_id, "lat": req.lat, "lon": req.lon, "gateway_id": node.gateway_id},
    )
    db.commit()

    security_logger.info("User '%s' placed node %d in tunnel %d", user.username, node.id, tunnel.id)
    latest_map = get_latest_readings(db, [node.id])
    return serialize_node(node, latest_map.get(node.id), time.time())


@router.delete("/api/nodes/{node_id}/placement")
def api_unplace_node(
    node_id: int,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Removes a node from surface placement back to unplaced status (superiors only)."""
    allowed_mines = get_allowed_mine_ids(db, user)

    node = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(Node.id == node_id, Gateway.mine_id.in_(allowed_mines))
        .first()
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    node.tunnel_id = None
    node.lat = None
    node.lon = None
    node.placed_by = None
    node.placed_at = None

    record_audit(
        db,
        user,
        action="node.unplace",
        entity="node",
        entity_id=node.id,
        detail={"gateway_id": node.gateway_id},
    )
    db.commit()

    security_logger.info("User '%s' unplaced node %d", user.username, node.id)
    latest_map = get_latest_readings(db, [node.id])
    return serialize_node(node, latest_map.get(node.id), time.time())
