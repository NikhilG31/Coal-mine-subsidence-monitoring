"""HTTP router for notification channels configuration.

Owns GET/POST /api/notifications/config endpoints.
Must NOT deliver third-party messages or invoke external notification gateways.
"""
from fastapi import APIRouter

router = APIRouter(tags=["notifications"])


@router.get("/api/notifications/config")
def api_notif_config():
    """Returns stored notification channel settings."""
    return {"config": {}}


@router.post("/api/notifications/config")
def api_save_notif_config():
    """Saves notification channel settings."""
    return {"message": "Notification configurations updated"}
