"""HTTP router for development simulation and demonstration controls.

Owns demo endpoints under /api/demo/*.
Must NOT run in production without ENABLE_DEMO_ROUTES enabled.
"""
from fastapi import APIRouter, Request
from app.dev.seed_demo_data import seed_demo_data
from app.dev.simulator import Simulator
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["demo"])
simulator_instance = Simulator()


@router.post("/api/demo/seed")
def api_demo_seed():
    """Seeds demo coal mine dataset for local development."""
    seed_demo_data()
    return {"message": "Demo coal mine datasets seeded (Jharia & Raniganj fields ready)"}


@router.post("/api/demo/step")
def api_demo_step():
    """Triggers a single simulation telemetry step."""
    count = simulator_instance.step()
    return {"message": "Single simulated step generated", "count": count}


@router.post("/api/demo/toggle-live")
async def api_demo_toggle(request: Request):
    """Toggles periodic live packet generation."""
    body = await request.body()
    data = await request.json() if body else {}
    enable = data.get("enable", not simulator_instance.is_running)
    if enable:
        simulator_instance.start_loop()
    else:
        simulator_instance.stop_loop()
    return {"running": simulator_instance.is_running}


@router.get("/api/demo/status")
def api_demo_status():
    """Returns current live simulation loop state."""
    return {"running": simulator_instance.is_running}


@router.post("/api/demo/trigger-hazard")
async def api_trigger_hazard(request: Request):
    """Injects a hazard override on a specific node."""
    data = await request.json()
    mac = norm_mac(data.get("gateway_mac_address", "A4:C1:38:90:12:01"))
    node_id = int(data.get("node_id", 3))
    level = str(data.get("level", "critical")).lower()
    simulator_instance.trigger_hazard(mac, node_id, level)
    simulator_instance.step()
    return {"message": f"Hazard override '{level}' triggered on Node {node_id} ({mac})"}
