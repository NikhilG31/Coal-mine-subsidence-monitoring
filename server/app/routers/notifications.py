"""HTTP router for notification settings, channel configurations, and delivery audit logs.

Owns REST endpoints for managing notification destinations, test alerts, and delivery history.
Must NOT execute network dispatches synchronously or store database transactions directly.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_configured_channels
from app.database import get_db
from app.models.mine import Mine
from app.models.notification_log import NotificationLog
from app.models.notification_settings import NotificationSettings
from app.models.user import User
from app.schemas.notifications import (
    TELEGRAM_CHAT_ID_REGEX,
    WHATSAPP_TO_REGEX,
    ChannelsConfiguredView,
    NotificationLogItem,
    NotificationSettingsResponse,
    NotificationTestResponse,
    UpdateNotificationSettingsRequest,
    mask_telegram_chat_id,
    mask_webhook_url,
    mask_whatsapp,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
)
from app.security.rate_limit import (
    check_notify_test_rate_limit,
    record_notify_test_attempt,
)
from app.security.roles import ROLE_MANAGER, SUPERIOR_ROLES
from app.services import clock, notification_service
from app.services.audit import record_audit
from app.services.webhook_guard import validate_webhook_url

_webhook_resolver = None


def set_webhook_resolver(resolver):
    """Sets an optional injectable resolver for webhook destination validation (e.g. in tests)."""
    global _webhook_resolver
    _webhook_resolver = resolver

router = APIRouter(tags=["notifications"])


def _get_scoped_mine(db: Session, user: User, mine_id: int) -> Mine:
    """Verifies that the mine exists and is within the user's tenant company and authorized scope."""
    allowed = get_allowed_mine_ids(db, user)
    if allowed is not None and mine_id not in allowed:
        raise HTTPException(status_code=404, detail="Mine not found")

    mine = (
        db.query(Mine)
        .filter(Mine.id == mine_id, Mine.company_id == user.company_id)
        .first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Mine not found")
    return mine


def _format_settings_response(mine_id: int, settings: Optional[NotificationSettings]) -> NotificationSettingsResponse:
    """Builds masked response model from notification_settings entity."""
    conf = get_configured_channels()
    if not settings:
        return NotificationSettingsResponse(
            mine_id=mine_id,
            telegram_enabled=False,
            telegram_chat_id=None,
            whatsapp_enabled=False,
            whatsapp_to=None,
            webhook_enabled=False,
            webhook_url=None,
            on_incident_open=True,
            on_incident_close=False,
            on_sos=True,
            on_offline=True,
            channels_configured=ChannelsConfiguredView(**conf),
            updated_by=None,
            updated_at=None,
        )

    return NotificationSettingsResponse(
        mine_id=mine_id,
        telegram_enabled=settings.telegram_enabled,
        telegram_chat_id=mask_telegram_chat_id(settings.telegram_chat_id),
        whatsapp_enabled=settings.whatsapp_enabled,
        whatsapp_to=mask_whatsapp(settings.whatsapp_to),
        webhook_enabled=settings.webhook_enabled,
        webhook_url=mask_webhook_url(settings.webhook_url),
        on_incident_open=settings.on_incident_open,
        on_incident_close=settings.on_incident_close,
        on_sos=settings.on_sos,
        on_offline=settings.on_offline,
        channels_configured=ChannelsConfiguredView(**conf),
        updated_by=settings.updated_by,
        updated_at=settings.updated_at,
    )


# =============================================================================
# 1. GET SETTINGS (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.get(
    "/api/mines/{id}/notification-settings",
    response_model=NotificationSettingsResponse,
)
def api_get_notification_settings(
    id: int,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns masked notification settings and channel availability for a mine."""
    mine = _get_scoped_mine(db, user, id)
    settings = (
        db.query(NotificationSettings)
        .filter(NotificationSettings.mine_id == mine.id)
        .first()
    )
    return _format_settings_response(mine.id, settings)


# =============================================================================
# 2. PUT SETTINGS (MANAGER ONLY, SCOPED)
# =============================================================================

@router.put(
    "/api/mines/{id}/notification-settings",
    response_model=NotificationSettingsResponse,
)
def api_put_notification_settings(
    id: int,
    req: UpdateNotificationSettingsRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Replaces notification settings and destinations for a mine (Manager only)."""
    mine = _get_scoped_mine(db, user, id)

    # Validate Telegram destination
    if req.telegram_enabled:
        if not req.telegram_chat_id or not req.telegram_chat_id.strip():
            raise HTTPException(
                status_code=422,
                detail="Telegram chat ID is required when Telegram is enabled",
            )
    if req.telegram_chat_id and req.telegram_chat_id.strip():
        if not TELEGRAM_CHAT_ID_REGEX.match(req.telegram_chat_id.strip()):
            raise HTTPException(status_code=422, detail="Invalid Telegram chat ID format")

    # Validate WhatsApp destination
    if req.whatsapp_enabled:
        if not req.whatsapp_to or not req.whatsapp_to.strip():
            raise HTTPException(
                status_code=422,
                detail="WhatsApp destination phone number is required when WhatsApp is enabled",
            )
    if req.whatsapp_to and req.whatsapp_to.strip():
        if not WHATSAPP_TO_REGEX.match(req.whatsapp_to.strip()):
            raise HTTPException(status_code=422, detail="Invalid WhatsApp phone number format")

    # Validate Webhook destination
    if req.webhook_enabled:
        if not req.webhook_url or not req.webhook_url.strip():
            raise HTTPException(
                status_code=422,
                detail="Webhook URL is required when Webhook is enabled",
            )
    if req.webhook_url and req.webhook_url.strip():
        try:
            validate_webhook_url(req.webhook_url.strip(), resolver=_webhook_resolver)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid webhook destination URL")

    settings = (
        db.query(NotificationSettings)
        .filter(NotificationSettings.mine_id == mine.id)
        .first()
    )

    now = clock.utcnow()
    tracked_fields = [
        "telegram_enabled",
        "telegram_chat_id",
        "whatsapp_enabled",
        "whatsapp_to",
        "webhook_enabled",
        "webhook_url",
        "on_incident_open",
        "on_incident_close",
        "on_sos",
        "on_offline",
    ]

    changed_fields = []
    if not settings:
        settings = NotificationSettings(mine_id=mine.id)
        db.add(settings)
        changed_fields = tracked_fields
    else:
        for f in tracked_fields:
            new_val = getattr(req, f)
            if isinstance(new_val, str):
                new_val = new_val.strip() or None
            old_val = getattr(settings, f)
            if old_val != new_val:
                changed_fields.append(f)

    settings.telegram_enabled = req.telegram_enabled
    settings.telegram_chat_id = req.telegram_chat_id.strip() if req.telegram_chat_id else None
    settings.whatsapp_enabled = req.whatsapp_enabled
    settings.whatsapp_to = req.whatsapp_to.strip() if req.whatsapp_to else None
    settings.webhook_enabled = req.webhook_enabled
    settings.webhook_url = req.webhook_url.strip() if req.webhook_url else None
    settings.on_incident_open = req.on_incident_open
    settings.on_incident_close = req.on_incident_close
    settings.on_sos = req.on_sos
    settings.on_offline = req.on_offline
    settings.updated_by = user.id
    settings.updated_at = now

    db.flush()

    record_audit(
        db=db,
        user=user,
        action="notification.settings_update",
        entity="notification_settings",
        entity_id=mine.id,
        detail={"changed_fields": sorted(changed_fields)},
        company_id=mine.company_id,
    )
    db.commit()
    db.refresh(settings)

    return _format_settings_response(mine.id, settings)


# =============================================================================
# 3. POST TEST NOTIFICATIONS (MANAGER ONLY, SCOPED)
# =============================================================================

@router.post(
    "/api/mines/{id}/notification-settings/test",
    response_model=NotificationTestResponse,
)
def api_test_notification_settings(
    id: int,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Enqueues test alert across all enabled channels for a mine (Manager only, 5/hr rate limit)."""
    mine = _get_scoped_mine(db, user, id)

    retry_after = check_notify_test_rate_limit(user.id)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded for test notifications",
            headers={"Retry-After": str(retry_after)},
        )
    record_notify_test_attempt(user.id)

    now = clock.utcnow()
    log_ids = notification_service.enqueue(
        db=db,
        mine_id=mine.id,
        event="test",
        ref_type="test",
        ref_id=None,
        message_args={"mine_name": mine.name},
        now=now,
    )

    record_audit(
        db=db,
        user=user,
        action="notification.test",
        entity="notification_settings",
        entity_id=mine.id,
        detail={"mine_id": mine.id, "created_ids": log_ids},
        company_id=mine.company_id,
    )
    db.commit()

    return NotificationTestResponse(
        message="Test notifications enqueued successfully",
        ids=log_ids,
    )


# =============================================================================
# 4. GET DELIVERY AUDIT LOG (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.get(
    "/api/notifications",
    response_model=List[NotificationLogItem],
)
def api_get_notifications_log(
    mine_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns delivery audit log records scoped to permitted mines (superiors only)."""
    allowed = get_allowed_mine_ids(db, user)

    query = (
        db.query(NotificationLog)
        .join(Mine, NotificationLog.mine_id == Mine.id)
        .filter(Mine.company_id == user.company_id)
    )

    if allowed is not None:
        query = query.filter(NotificationLog.mine_id.in_(allowed))

    if mine_id is not None:
        if allowed is not None and mine_id not in allowed:
            raise HTTPException(status_code=404, detail="Mine not found")
        # Also check company scoping for mine_id
        target_mine = db.query(Mine).filter(Mine.id == mine_id, Mine.company_id == user.company_id).first()
        if not target_mine:
            raise HTTPException(status_code=404, detail="Mine not found")
        query = query.filter(NotificationLog.mine_id == mine_id)

    if status is not None:
        if status not in ("pending", "sent", "failed", "skipped"):
            raise HTTPException(status_code=422, detail=f"Invalid status '{status}'")
        query = query.filter(NotificationLog.status == status)

    records = (
        query.order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
        .limit(limit)
        .all()
    )

    return [
        NotificationLogItem(
            id=r.id,
            mine_id=r.mine_id,
            event=r.event,
            channel=r.channel,
            status=r.status,
            attempts=r.attempts,
            created_at=r.created_at,
            sent_at=r.sent_at,
            result=r.result,
            message=r.message,
        )
        for r in records
    ]
