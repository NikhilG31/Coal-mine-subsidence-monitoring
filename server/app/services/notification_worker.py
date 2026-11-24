"""Background worker service for notification outbox processing.

Owns claiming due notification outbox rows, invoking channel dispatch adapters, and committing per-row delivery state.
Must NOT handle HTTP requests, import FastAPI components, or leak credentials into logs or results.
"""
import datetime
import logging
from typing import Callable, Dict, List, Optional
import httpx
from sqlalchemy.orm import Session

from app.config import (
    NOTIFICATIONS_ENABLED,
    NOTIFY_BACKOFF_BASE_S,
    NOTIFY_MAX_ATTEMPTS,
)
from app.models.mine import Mine
from app.models.notification_settings import NotificationSettings
from app.services import clock
from app.services.notification_channels import SendResult, send
from app.services.notification_service import (
    claim_due,
    mark_failed,
    mark_retry,
    mark_sent,
    purge_old,
)

logger = logging.getLogger(__name__)

__all__ = ["run_notification_cycle"]

_last_purge_at: Optional[datetime.datetime] = None


def run_notification_cycle(
    db: Session,
    now: Optional[datetime.datetime] = None,
    transport: Optional[httpx.BaseTransport] = None,
    resolver: Optional[Callable[[str], List[str]]] = None,
) -> Dict[str, int]:
    """Claims due notification outbox rows and delivers them with per-row transaction commits."""
    global _last_purge_at

    counts = {"sent": 0, "retried": 0, "failed": 0, "skipped": 0}
    if not NOTIFICATIONS_ENABLED:
        return counts

    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    # 1. Claim due rows with SKIP LOCKED (limit 50)
    try:
        due_rows = claim_due(db, now, limit=50)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to claim due notifications: %s", exc)
        return counts

    # 2. Deliver each row and commit per row
    for row in due_rows:
        try:
            settings = (
                db.query(NotificationSettings)
                .filter(NotificationSettings.mine_id == row.mine_id)
                .first()
            )
            mine = db.query(Mine).filter(Mine.id == row.mine_id).first()

            dest: Optional[str] = None
            if row.channel == "telegram":
                dest = settings.telegram_chat_id if settings else None
            elif row.channel == "whatsapp":
                dest = settings.whatsapp_to if settings else None
            elif row.channel == "webhook":
                dest = settings.webhook_url if settings else None

            if not dest or not dest.strip():
                mark_failed(db, row.id, "Destination not set in mine settings")
                db.commit()
                counts["failed"] += 1
                continue

            extra = {
                "event": row.event,
                "mine": mine.name if mine else f"Mine {row.mine_id}",
                "id": row.id,
            }

            result: SendResult = send(
                channel=row.channel,
                destination=dest,
                message=row.message,
                now=now,
                transport=transport,
                resolver=resolver,
                extra_payload=extra,
            )

            if result.ok:
                mark_sent(db, row.id, now, result.detail)
                counts["sent"] += 1
            elif result.retryable:
                new_attempts = row.attempts + 1
                if new_attempts >= NOTIFY_MAX_ATTEMPTS:
                    mark_failed(db, row.id, result.detail)
                    counts["failed"] += 1
                else:
                    delay_s = min(NOTIFY_BACKOFF_BASE_S * (2 ** (new_attempts - 1)), 600)
                    next_attempt = now + datetime.timedelta(seconds=delay_s)
                    mark_retry(db, row.id, next_attempt, result.detail)
                    counts["retried"] += 1
            else:
                mark_failed(db, row.id, result.detail)
                counts["failed"] += 1

            db.commit()

        except Exception as exc:
            db.rollback()
            logger.exception("Error delivering notification row %d: %s", row.id, exc)
            counts["failed"] += 1

    # 3. Periodic purge once per hour
    try:
        if _last_purge_at is None or (now - _last_purge_at) >= datetime.timedelta(hours=1):
            purge_old(db, now)
            db.commit()
            _last_purge_at = now
    except Exception as exc:
        db.rollback()
        logger.exception("Error pruning expired notification logs: %s", exc)

    return counts
