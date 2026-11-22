"""HTTP router for coal mine sites, underground tunnels, and gateway provisioning.

Owns /api/mines, /api/mines/{id}/tunnels, and /api/mines/{id}/gateways endpoints.
Must NOT handle telemetry packet ingestion, raw socket I/O, or worker shift management.
"""
import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import OFFLINE_AFTER_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.schemas.mines import (
    CreateGatewayRequest,
    CreateMineRequest,
    CreateTunnelRequest,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
    security_logger,
)
from app.security.roles import ROLE_MANAGER
from app.services.audit import record_audit
from app.services.connectivity import gateway_is_online
from app.services.gateway_keys import generate_key, hash_key

router = APIRouter(tags=["mines"])


# =============================================================================
# MINE ENDPOINTS
# =============================================================================

@router.get("/api/mines")
def api_get_mines(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns mines permitted to the caller (all roles, scoped)."""
    allowed_mine_ids = get_allowed_mine_ids(db, user)
    mines = (
        db.query(Mine)
        .filter(Mine.id.in_(allowed_mine_ids))
        .order_by(Mine.id.asc())
        .all()
    )
    return {
        "mines": [
            {
                "id": m.id,
                "name": m.name,
                "subsidiary": m.subsidiary,
                "status": m.status,
                "method": m.method,
                "lat": m.lat,
                "lon": m.lon,
                "area_ha": m.area_ha,
                "max_depth_m": m.max_depth_m,
            }
            for m in mines
        ]
    }


@router.post("/api/mines")
def api_create_mine(
    req: CreateMineRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Creates a new coal mine site for the manager's company and assigns creator."""
    existing = (
        db.query(Mine)
        .filter(Mine.company_id == user.company_id, Mine.name == req.name)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Mine with name '{req.name}' already exists in your company",
        )

    mine = Mine(
        company_id=user.company_id,
        name=req.name,
        subsidiary=req.subsidiary.strip() if req.subsidiary else None,
        status=req.status,
        method=req.method,
        area_ha=req.area_ha,
        max_depth_m=req.max_depth_m,
        lat=req.lat,
        lon=req.lon,
    )
    db.add(mine)
    db.flush()

    # Automatically add creator to user_mines so it appears in allowed_mine_ids immediately
    db.add(UserMine(user_id=user.id, mine_id=mine.id))

    record_audit(
        db,
        user,
        action="mine.create",
        entity="mine",
        entity_id=mine.id,
        detail={"name": mine.name, "company_id": user.company_id},
    )
    db.commit()

    security_logger.info("Manager '%s' created mine '%s' (id=%d)", user.username, mine.name, mine.id)
    return {
        "id": mine.id,
        "name": mine.name,
        "subsidiary": mine.subsidiary,
        "status": mine.status,
        "method": mine.method,
        "area_ha": mine.area_ha,
        "max_depth_m": mine.max_depth_m,
        "lat": mine.lat,
        "lon": mine.lon,
    }


# =============================================================================
# TUNNEL ENDPOINTS
# =============================================================================

@router.get("/api/mines/{mine_id}/tunnels")
def api_get_tunnels(
    mine_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns underground tunnels for a mine (all roles, scoped)."""
    allowed_mine_ids = get_allowed_mine_ids(db, user)
    if mine_id not in allowed_mine_ids:
        raise HTTPException(status_code=404, detail="Mine not found")

    tunnels = (
        db.query(Tunnel)
        .filter(Tunnel.mine_id == mine_id)
        .order_by(Tunnel.id.asc())
        .all()
    )
    return {
        "tunnels": [
            {
                "id": t.id,
                "name": t.name,
                "code": t.code,
                "depth_m": t.depth_m,
                "status": t.status,
                "mine_id": t.mine_id,
            }
            for t in tunnels
        ]
    }


@router.post("/api/mines/{mine_id}/tunnels")
def api_create_tunnel(
    mine_id: int,
    req: CreateTunnelRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Adds a new underground tunnel to a mine (Manager only)."""
    mine = (
        db.query(Mine)
        .filter(Mine.id == mine_id, Mine.company_id == user.company_id)
        .first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Mine not found")

    existing_code = (
        db.query(Tunnel)
        .filter(Tunnel.mine_id == mine_id, Tunnel.code == req.code)
        .first()
    )
    if existing_code:
        raise HTTPException(
            status_code=409,
            detail=f"Tunnel with code '{req.code}' already exists in this mine",
        )

    tunnel = Tunnel(
        mine_id=mine_id,
        name=req.name,
        code=req.code,
        depth_m=req.depth_m,
        status=req.status,
    )
    db.add(tunnel)
    db.flush()

    record_audit(
        db,
        user,
        action="tunnel.create",
        entity="tunnel",
        entity_id=tunnel.id,
        detail={"name": tunnel.name, "code": tunnel.code, "mine_id": mine_id},
    )
    db.commit()

    security_logger.info(
        "Manager '%s' created tunnel '%s' (id=%d) in mine %d",
        user.username,
        tunnel.name,
        tunnel.id,
        mine_id,
    )
    return {
        "id": tunnel.id,
        "name": tunnel.name,
        "code": tunnel.code,
        "depth_m": tunnel.depth_m,
        "status": tunnel.status,
        "mine_id": tunnel.mine_id,
    }


# =============================================================================
# GATEWAY PROVISIONING IN MINE
# =============================================================================

@router.post("/api/mines/{mine_id}/gateways")
def api_create_gateway(
    mine_id: int,
    req: CreateGatewayRequest,
    response: Response,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Registers a hardware gateway and displays its API key once (Manager only)."""
    mine = (
        db.query(Mine)
        .filter(Mine.id == mine_id, Mine.company_id == user.company_id)
        .first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Mine not found")

    # Global unique check on MAC
    existing_gw = db.query(Gateway).filter(Gateway.mac == req.mac).first()
    if existing_gw:
        raise HTTPException(
            status_code=409,
            detail="A gateway with this MAC address already exists",
        )

    raw_key = generate_key()
    key_hash = hash_key(raw_key)

    gw = Gateway(
        mac=req.mac,
        mine_id=mine_id,
        name=req.name,
        api_key_hash=key_hash,
        active=True,
        created_by=user.id,
    )
    db.add(gw)
    db.flush()

    record_audit(
        db,
        user,
        action="gateway.register",
        entity="gateway",
        entity_id=gw.id,
        detail={"name": gw.name, "mac": gw.mac, "mine_id": mine_id},
    )
    db.commit()

    response.headers["Cache-Control"] = "no-store"
    security_logger.info(
        "Manager '%s' registered gateway '%s' (id=%d, MAC=%s)",
        user.username,
        gw.name,
        gw.id,
        gw.mac,
    )
    return {
        "id": gw.id,
        "name": gw.name,
        "mac": gw.mac,
        "active": gw.active,
        "mine_id": gw.mine_id,
        "api_key": raw_key,
    }


@router.get("/api/mines/{mine_id}/gateways")
def api_get_mine_gateways(
    mine_id: int,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Lists hardware gateways bound to a mine (Manager only; excludes key/hash)."""
    mine = (
        db.query(Mine)
        .filter(Mine.id == mine_id, Mine.company_id == user.company_id)
        .first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Mine not found")

    gateways = (
        db.query(Gateway)
        .filter(Gateway.mine_id == mine_id)
        .order_by(Gateway.id.asc())
        .all()
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for g in gateways:
        online = gateway_is_online(g.last_seen_at, now)

        out.append(
            {
                "id": g.id,
                "name": g.name,
                "mac": g.mac,
                "active": g.active,
                "created_by": g.created_by,
                "last_seen_at": g.last_seen_at.isoformat() if g.last_seen_at else None,
                "online": online,
                "node_count": len(g.nodes),
            }
        )
    return {"gateways": out}
