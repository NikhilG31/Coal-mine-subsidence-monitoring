"""HTTP router for hardware gateway topology and hierarchy.

Owns the GET /api/gateways endpoint and aggregation across gateway branches.
Must NOT store readings, execute migrations, or handle node placement directly.
"""
import time
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import OFFLINE_AFTER_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.services.hazard_classifier import LEVEL_NORMAL
from app.services.node_serializer import serialize_node

router = APIRouter(tags=["gateways"])


@router.get("/api/gateways")
def api_gateways(db: Session = Depends(get_db)):
    """Returns the gateway hierarchy and node inventory."""
    now_ts = time.time()
    nodes = db.query(Node).order_by(Node.gateway_id, Node.node_no).all()
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

    gateways = db.query(Gateway).all()
    tree = {}
    for gw in gateways:
        tree[gw.mac] = {
            "gateway_mac_address": gw.mac,
            "gateway_name": gw.name,
            "node_count": 0,
            "online_nodes": 0,
            "placed_nodes": 0,
            "worst_status": LEVEL_NORMAL,
            "last_seen_age_s": (
                round(now_ts - gw.last_seen_at.timestamp(), 1) if gw.last_seen_at else None
            ),
            "nodes": [],
        }

    status_ranks = {"normal": 0, "watch": 1, "warning": 2, "critical": 3}
    for n in nodes_list:
        mac = n["gateway_mac_address"]
        if mac not in tree:
            tree[mac] = {
                "gateway_mac_address": mac,
                "gateway_name": n.get("gateway") or f"Gateway {mac[-8:]}",
                "node_count": 0,
                "online_nodes": 0,
                "placed_nodes": 0,
                "worst_status": LEVEL_NORMAL,
                "last_seen_age_s": None,
                "nodes": [],
            }
        g = tree[mac]
        g["nodes"].append(n)
        g["node_count"] += 1
        g["online_nodes"] += int(n["online"])
        g["placed_nodes"] += int(n["placed"])
        if g["last_seen_age_s"] is None or n["age_s"] < g["last_seen_age_s"]:
            g["last_seen_age_s"] = n["age_s"]

    for g in tree.values():
        g["online"] = g["online_nodes"] > 0
        if g["nodes"]:
            worst = max(g["nodes"], key=lambda node: status_ranks.get(node["status"], 0))
            g["worst_status"] = worst["status"]

    return {"now": now_ts, "gateways": list(tree.values())}
