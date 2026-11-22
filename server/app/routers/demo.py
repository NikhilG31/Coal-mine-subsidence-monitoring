"""HTTP router for development simulation and demonstration controls.

Owns demo endpoints under /api/demo/* with Manager authorization and feature-flag checks.
Must NOT run in production without ENABLE_DEMO_ROUTES enabled.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.config import ENABLE_DEMO_ROUTES
from app.dev.seed_demo_data import seed_demo_data
from app.dev.simulator import Simulator
from app.models.user import User
from app.security.dependencies import get_current_user, require_role
from app.security.roles import ROLE_MANAGER
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["demo"])
simulator_instance = Simulator()


def verify_demo_access(user: User = Depends(require_role(ROLE_MANAGER))) -> User:
    """Verifies that demo routes are enabled and caller is a Manager; returns 404 otherwise."""
    if not ENABLE_DEMO_ROUTES:
        raise HTTPException(status_code=404, detail="Demo routes are disabled")
    return user


def verify_demo_enabled(user: User = Depends(get_current_user)) -> User:
    """Verifies that demo routes are enabled and caller is authenticated; returns 404 otherwise."""
    if not ENABLE_DEMO_ROUTES:
        raise HTTPException(status_code=404, detail="Demo routes are disabled")
    return user


@router.post("/api/demo/seed")
def api_demo_seed(user: User = Depends(verify_demo_access)):
    """Seeds demo coal mine dataset for local development (Manager only)."""
    seed_demo_data()
    return {"message": "Demo coal mine datasets seeded (Jharia & Raniganj fields ready)"}


@router.post("/api/demo/step")
def api_demo_step(user: User = Depends(verify_demo_access)):
    """Triggers a single simulation telemetry step (Manager only)."""
    count = simulator_instance.step()
    return {"message": "Single simulated step generated", "count": count}


@router.post("/api/demo/toggle-live")
async def api_demo_toggle(request: Request, user: User = Depends(verify_demo_access)):
    """Toggles periodic live packet generation (Manager only)."""
    body = await request.body()
    data = await request.json() if body else {}
    enable = data.get("enable", not simulator_instance.is_running)
    if enable:
        simulator_instance.start_loop()
    else:
        simulator_instance.stop_loop()
    return {"running": simulator_instance.is_running}


@router.get("/api/demo/status")
def api_demo_status(user: User = Depends(verify_demo_enabled)):
    """Returns current live simulation loop state (any authenticated user)."""
    return {"running": simulator_instance.is_running}


class TriggerHazardRequest(BaseModel):
    gateway_mac_address: Optional[str] = "A4:C1:38:90:12:01"
    node_id: Optional[int] = 3
    level: Optional[str] = "critical"


@router.post("/api/demo/trigger-hazard")
def api_trigger_hazard(
    req: Optional[TriggerHazardRequest] = None,
    user: User = Depends(verify_demo_access),
):
    """Injects a hazard override on a specific node (Manager only)."""
    payload = req or TriggerHazardRequest()
    mac = norm_mac(payload.gateway_mac_address or "A4:C1:38:90:12:01")
    node_id = int(payload.node_id or 3)
    level = str(payload.level or "critical").lower()
    simulator_instance.trigger_hazard(mac, node_id, level)
    simulator_instance.step()
    return {"message": f"Hazard override '{level}' triggered on Node {node_id} ({mac})"}
