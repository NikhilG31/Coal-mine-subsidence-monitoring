"""HTTP router for subsidence alerts and notifications tracking.

Owns GET /api/alerts and POST /api/alerts/{alert_id}/ack endpoints with superior role authorization.
Must NOT deliver third-party webhooks or cellular SMS messages.
"""
from fastapi import APIRouter, Depends
from app.models.user import User
from app.security.dependencies import require_role
from app.security.roles import SUPERIOR_ROLES

router = APIRouter(tags=["alerts"])


@router.get("/api/alerts")
def api_alerts(user: User = Depends(require_role(*SUPERIOR_ROLES))):
    """Returns current active hazard alerts (superiors only)."""
    return {"alerts": []}


@router.post("/api/alerts/{alert_id}/ack")
def api_ack_alert(alert_id: int, user: User = Depends(require_role(*SUPERIOR_ROLES))):
    """Acknowledges an active hazard alert by identifier (superiors only)."""
    return {"message": "Alert acknowledged", "id": alert_id}
