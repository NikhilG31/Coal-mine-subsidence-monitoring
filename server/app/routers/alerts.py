"""HTTP router for subsidence alerts and notifications tracking.

Owns GET /api/alerts and POST /api/alerts/{alert_id}/ack endpoints.
Must NOT deliver third-party webhooks or cellular SMS messages.
"""
from fastapi import APIRouter

router = APIRouter(tags=["alerts"])


@router.get("/api/alerts")
def api_alerts():
    """Returns current active hazard alerts."""
    return {"alerts": []}


@router.post("/api/alerts/{alert_id}/ack")
def api_ack_alert(alert_id: int):
    """Acknowledges an active hazard alert by identifier."""
    return {"message": "Alert acknowledged", "id": alert_id}
