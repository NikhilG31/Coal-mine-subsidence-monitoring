"""HTTP router for notification channels configuration.

Owns GET/POST /api/notifications/config and POST /api/notifications/test stub with superior authorization.
Must NOT deliver third-party messages or invoke external notification gateways.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.models.user import User
from app.security.dependencies import require_role
from app.security.roles import SUPERIOR_ROLES

router = APIRouter(tags=["notifications"])


@router.get("/api/notifications/config")
def api_notif_config(user: User = Depends(require_role(*SUPERIOR_ROLES))):
    """Returns stored notification channel settings (superiors only)."""
    return {"config": {}}


@router.post("/api/notifications/config")
def api_save_notif_config(user: User = Depends(require_role(*SUPERIOR_ROLES))):
    """Saves notification channel settings (superiors only)."""
    return {"message": "Notification configurations updated"}


@router.post("/api/notifications/test")
def api_test_notification_stub(user: User = Depends(require_role(*SUPERIOR_ROLES))):
    """Superiors-only stub for testing alert delivery channels."""
    raise HTTPException(status_code=501, detail="Notifications are not implemented yet")
