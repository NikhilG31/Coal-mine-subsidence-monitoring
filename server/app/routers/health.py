"""HTTP router for health checks and service monitoring.

Owns the GET /api/health endpoint.
Must NOT execute mutations or create database transactions.
"""
from fastapi import APIRouter
from app.services.packet_codec import PAYLOAD_SIZE

router = APIRouter(tags=["health"])


@router.get("/api/health")
def api_health():
    """Returns application health and subsystem statuses."""
    return {
        "status": "healthy",
        "system": "Coal Mine Subsidence Monitoring Server",
        "database": "PostgreSQL",
        "ml_source": "rule_based_classifier",
        "payload_size": PAYLOAD_SIZE,
        "simulator_active": False,
    }
