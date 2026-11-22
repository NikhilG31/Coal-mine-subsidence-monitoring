"""HTTP router for hardware gateway topology, provisioning, and key lifecycle.

Owns GET/PATCH/DELETE /api/gateways and /api/gateways/{id}/rotate-key endpoints.
Must NOT store telemetry readings, alter packet formats, or expose API keys in list responses.
"""
import datetime
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.config import OFFLINE_AFTER_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.user import User
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
    security_logger,
)
from app.security.roles import ROLE_MANAGER, SUPERIOR_ROLES
from app.services.audit import record_audit
from app.services.connectivity import gateway_is_online
from app.services.gateway_keys import generate_key, hash_key
from app.services.hazard_classifier import LEVEL_NORMAL
from app.services.latest_readings import get_latest_readings
from app.services.node_serializer import serialize_node

router = APIRouter(tags=["gateways"])


class PatchGatewayRequest(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not (1 <= len(clean) <= 255):
                raise ValueError("Gateway name must be between 1 and 255 characters")
            return clean
        return v


@router.get("/api/gateways")
def api_gateways(
    mine_id: Optional[int] = None,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns the gateway hierarchy and node inventory scoped to permitted mines (superiors only)."""
    now_ts = time.time()
    now_dt = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc)
    allowed_mines = get_allowed_mine_ids(db, user)

    if mine_id is not None:
        if mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Mine not found")
        scoped_mines = [mine_id]
    else:
        scoped_mines = allowed_mines

    nodes = (
        db.query(Node)
        .join(Gateway, Gateway.id == Node.gateway_id)
        .filter(Gateway.mine_id.in_(scoped_mines))
        .order_by(Node.gateway_id, Node.node_no)
        .all()
    )

    node_ids = [n.id for n in nodes]
    latest_map = get_latest_readings(db, node_ids)

    nodes_list = [
        serialize_node(n, latest_map.get(n.id), now_ts, offline_after_s=OFFLINE_AFTER_S)
        for n in nodes
    ]

    gateways = db.query(Gateway).filter(Gateway.mine_id.in_(scoped_mines)).all()
    tree = {}
    for gw in gateways:
        tree[gw.mac] = {
            "id": gw.id,
            "mine_id": gw.mine_id,
            "gateway_mac_address": gw.mac,
            "gateway_name": gw.name,
            "active": gw.active,
            "online": gateway_is_online(gw.last_seen_at, now_dt),
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
        if mac in tree:
            g = tree[mac]
            g["nodes"].append(n)
            g["node_count"] += 1
            g["online_nodes"] += int(n["online"])
            g["placed_nodes"] += int(n["placed"])
            if g["last_seen_age_s"] is None or n["age_s"] < g["last_seen_age_s"]:
                g["last_seen_age_s"] = n["age_s"]

    for g in tree.values():
        if g["nodes"]:
            worst = max(g["nodes"], key=lambda node: status_ranks.get(node["status"], 0))
            g["worst_status"] = worst["status"]

    return {"now": now_ts, "gateways": list(tree.values())}


@router.patch("/api/gateways/{gateway_id}")
def api_patch_gateway(
    gateway_id: int,
    req: PatchGatewayRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Updates gateway name or active status (Manager only, within company)."""
    gw = (
        db.query(Gateway)
        .join(Mine, Mine.id == Gateway.mine_id)
        .filter(Gateway.id == gateway_id, Mine.company_id == user.company_id)
        .first()
    )
    if not gw:
        raise HTTPException(status_code=404, detail="Gateway not found")

    changes = {}
    if req.name is not None:
        gw.name = req.name
        changes["name"] = req.name
    if req.active is not None:
        gw.active = req.active
        changes["active"] = req.active

    action = "gateway.disable" if req.active is False else "gateway.update"
    record_audit(
        db,
        user,
        action=action,
        entity="gateway",
        entity_id=gw.id,
        detail=changes,
    )
    db.commit()

    security_logger.info("Manager '%s' updated gateway %d (%s)", user.username, gw.id, changes)
    return {
        "id": gw.id,
        "name": gw.name,
        "mac": gw.mac,
        "active": gw.active,
        "mine_id": gw.mine_id,
    }


@router.post("/api/gateways/{gateway_id}/rotate-key")
def api_rotate_gateway_key(
    gateway_id: int,
    response: Response,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Generates a new API key for the gateway, invalidating previous keys (Manager only)."""
    gw = (
        db.query(Gateway)
        .join(Mine, Mine.id == Gateway.mine_id)
        .filter(Gateway.id == gateway_id, Mine.company_id == user.company_id)
        .first()
    )
    if not gw:
        raise HTTPException(status_code=404, detail="Gateway not found")

    raw_key = generate_key()
    gw.api_key_hash = hash_key(raw_key)

    record_audit(
        db,
        user,
        action="gateway.rotate_key",
        entity="gateway",
        entity_id=gw.id,
        detail={},
    )
    db.commit()

    response.headers["Cache-Control"] = "no-store"
    security_logger.info("Manager '%s' rotated API key for gateway %d (%s)", user.username, gw.id, gw.mac)
    return {
        "id": gw.id,
        "name": gw.name,
        "mac": gw.mac,
        "active": gw.active,
        "mine_id": gw.mine_id,
        "api_key": raw_key,
    }


@router.delete("/api/gateways/{gateway_id}")
def api_delete_gateway(
    gateway_id: int,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Disables a hardware gateway (Manager only; sets active=false)."""
    gw = (
        db.query(Gateway)
        .join(Mine, Mine.id == Gateway.mine_id)
        .filter(Gateway.id == gateway_id, Mine.company_id == user.company_id)
        .first()
    )
    if not gw:
        raise HTTPException(status_code=404, detail="Gateway not found")

    gw.active = False
    record_audit(
        db,
        user,
        action="gateway.disable",
        entity="gateway",
        entity_id=gw.id,
        detail={"active": False},
    )
    db.commit()

    security_logger.info("Manager '%s' disabled gateway %d (%s)", user.username, gw.id, gw.mac)
    return {
        "id": gw.id,
        "name": gw.name,
        "mac": gw.mac,
        "active": gw.active,
        "mine_id": gw.mine_id,
    }
