"""HTTP router for node geospatial placement and unplaced queue management.

Owns GET/POST /api/placements, DELETE /api/placements/{gateway}/{node_id}, and GET /api/unplaced.
Must NOT decode raw telemetry packets or issue user authentication tokens.
"""
import datetime
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import OFFLINE_AFTER_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.models.tunnel import Tunnel
from app.services.node_serializer import serialize_node
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["placements"])


@router.get("/api/placements")
def api_placements(db: Session = Depends(get_db)):
    """Returns placed nodes and their coordinates."""
    placed = (
        db.query(Node)
        .filter(Node.lat.isnot(None), Node.lon.isnot(None))
        .all()
    )
    res = []
    for n in placed:
        mac = n.gateway.mac if n.gateway else "UNKNOWN"
        loc = n.tunnel.name if n.tunnel else f"Node {n.node_no}"
        res.append(
            {
                "gateway_mac_address": mac,
                "node_id": n.node_no,
                "location_name": loc,
                "latitude": n.lat,
                "longitude": n.lon,
                "placed_at": n.placed_at.timestamp() if n.placed_at else time.time(),
                "placed_by": n.placed_by,
            }
        )
    return {"placements": res}


@router.post("/api/placements")
async def api_save_placement(request: Request, db: Session = Depends(get_db)):
    """Saves geographic coordinates for a node."""
    data = await request.json()
    mac = norm_mac(data.get("gateway_mac_address"))
    node_no = int(data.get("node_id", 0))
    lat = float(data.get("latitude", 0.0))
    lng = float(data.get("longitude", 0.0))

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid latitude/longitude coordinates")

    gateway = db.query(Gateway).filter(Gateway.mac == mac).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    node = (
        db.query(Node)
        .filter(Node.gateway_id == gateway.id, Node.node_no == node_no)
        .first()
    )
    if not node:
        node = Node(gateway_id=gateway.id, node_no=node_no)
        db.add(node)

    node.lat = lat
    node.lon = lng
    node.placed_at = datetime.datetime.now(datetime.timezone.utc)

    # Link to mine's first tunnel if tunnel not assigned
    if node.tunnel_id is None:
        first_tunnel = db.query(Tunnel).filter(Tunnel.mine_id == gateway.mine_id).first()
        if first_tunnel:
            node.tunnel_id = first_tunnel.id

    db.commit()
    return {"message": "Node location saved", "node_id": node_no, "gateway_mac": mac}


@router.delete("/api/placements/{gateway}/{node_id}")
def api_delete_placement(gateway: str, node_id: int, db: Session = Depends(get_db)):
    """Unplaces a node from the map."""
    mac = norm_mac(gateway)
    gw = db.query(Gateway).filter(Gateway.mac == mac).first()
    if gw:
        node = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == node_id).first()
        if node:
            node.lat = None
            node.lon = None
            node.tunnel_id = None
            node.placed_at = None
            db.commit()
    return {"message": "Node unplaced from map"}


@router.get("/api/unplaced")
def api_unplaced(db: Session = Depends(get_db)):
    """Returns nodes reporting data that have not yet been placed on the surface map."""
    now_ts = time.time()
    unplaced_nodes = (
        db.query(Node)
        .filter((Node.lat.is_(None)) | (Node.lon.is_(None)) | (Node.tunnel_id.is_(None)))
        .all()
    )
    node_ids = [n.id for n in unplaced_nodes]
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

    serialized = [
        serialize_node(n, latest_map.get(n.id), now_ts, offline_after_s=OFFLINE_AFTER_S)
        for n in unplaced_nodes
    ]
    return {"unplaced": serialized}
