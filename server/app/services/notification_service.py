"""Notification transactional outbox and persistence service.

Owns enqueuing notification events in caller sessions, claiming due rows with SKIP LOCKED, and status transitions.
Must NOT commit caller transactions, make direct HTTP calls, or leak worker identities.
"""
import datetime
import logging
from typing import List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import (
    DASHBOARD_BASE_URL,
    NOTIFICATIONS_ENABLED,
    NOTIFY_LOG_RETENTION_DAYS,
    get_configured_channels,
)
from app.models.mine import Mine
from app.models.notification_log import NotificationLog
from app.models.notification_settings import NotificationSettings
from app.services import clock
from app.services.notification_messages import build_message

logger = logging.getLogger(__name__)

__all__ = [
    "claim_due",
    "enqueue",
    "mark_failed",
    "mark_retry",
    "mark_sent",
    "purge_old",
]


def enqueue(
    db: Session,
    mine_id: int,
    event: str,
    ref_type: Optional[str],
    ref_id: Optional[int],
    message_args: dict,
    now: Optional[datetime.datetime] = None,
) -> List[int]:
    """Inserts pending notification outbox rows into the caller's transaction without committing.

    Best-effort savepoint isolation: catches and logs any internal error, never raising into caller.
    """
    if not NOTIFICATIONS_ENABLED:
        return []

    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    created_ids: List[int] = []

    try:
        with db.begin_nested():
            settings = (
                db.query(NotificationSettings)
                .filter(NotificationSettings.mine_id == mine_id)
                .first()
            )
            if not settings:
                return []

            # Check event-specific toggles
            if event == "incident_open" and not settings.on_incident_open:
                return []
            if event == "incident_close" and not settings.on_incident_close:
                return []
            if event == "sos_report" and not settings.on_sos:
                return []
            if event == "offline_alert" and not settings.on_offline:
                return []

            # Construct dedupe key
            if event == "test":
                dedupe_key = f"test:mine:{mine_id}:{now.strftime('%Y%m%d%H%M')}"
            else:
                dedupe_key = f"{event}:{ref_type or 'none'}:{ref_id or 0}"

            # Dashboard link formatting
            link = None
            if DASHBOARD_BASE_URL:
                link = f"{DASHBOARD_BASE_URL.rstrip('/')}/"

            mine = db.query(Mine).filter(Mine.id == mine_id).first()
            mine_name = message_args.get("mine_name") or (mine.name if mine else f"Mine {mine_id}")

            message = build_message(
                event=event,
                mine_name=mine_name,
                tunnel_name=message_args.get("tunnel_name"),
                node_label=message_args.get("node_label"),
                level=message_args.get("level"),
                counts=message_args.get("counts"),
                now=now,
                link=link,
            )

            configured = get_configured_channels()
            channel_specs = [
                ("telegram", settings.telegram_enabled),
                ("whatsapp", settings.whatsapp_enabled),
                ("webhook", settings.webhook_enabled),
            ]

            incident_id = message_args.get("incident_id")
            if incident_id is None and ref_type == "incident" and ref_id:
                incident_id = ref_id

            for channel_name, enabled in channel_specs:
                if not enabled:
                    continue

                is_conf = configured.get(channel_name, False)
                if not is_conf:
                    row_status = "skipped"
                    row_result = "channel not configured"
                else:
                    row_status = "pending"
                    row_result = None

                stmt = (
                    pg_insert(NotificationLog)
                    .values(
                        mine_id=mine_id,
                        event=event,
                        ref_type=ref_type,
                        ref_id=ref_id,
                        incident_id=incident_id,
                        channel=channel_name,
                        status=row_status,
                        attempts=0,
                        next_attempt_at=now,
                        created_at=now,
                        sent_at=None,
                        result=row_result,
                        message=message,
                        dedupe_key=dedupe_key,
                    )
                    .on_conflict_do_nothing(index_elements=["dedupe_key", "channel"])
                    .returning(NotificationLog.id)
                )
                res = db.execute(stmt)
                scalar_id = res.scalar()
                if scalar_id is not None:
                    created_ids.append(scalar_id)

    except Exception as exc:
        logger.error(
            "Notification enqueue failed for mine %d event %s: %s",
            mine_id,
            event,
            exc,
            exc_info=True,
        )
        return []

    return created_ids


def claim_due(
    db: Session,
    now: datetime.datetime,
    limit: int = 50,
) -> List[NotificationLog]:
    """Selects and locks pending notification records due for delivery using SKIP LOCKED."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    return (
        db.query(NotificationLog)
        .filter(
            NotificationLog.status == "pending",
            NotificationLog.next_attempt_at <= now,
        )
        .order_by(NotificationLog.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )


def mark_sent(
    db: Session,
    log_id: int,
    now: datetime.datetime,
    detail: str,
) -> None:
    """Updates log row to sent state with sanitized outcome and timestamp."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    db.execute(
        update(NotificationLog)
        .where(NotificationLog.id == log_id)
        .values(
            status="sent",
            sent_at=now,
            attempts=NotificationLog.attempts + 1,
            result=(detail or "OK")[:200],
        )
    )


def mark_retry(
    db: Session,
    log_id: int,
    next_attempt_at: datetime.datetime,
    detail: str,
) -> None:
    """Updates log row with next backoff retry timestamp and outcome detail."""
    if next_attempt_at.tzinfo is None:
        next_attempt_at = next_attempt_at.replace(tzinfo=datetime.timezone.utc)

    db.execute(
        update(NotificationLog)
        .where(NotificationLog.id == log_id)
        .values(
            attempts=NotificationLog.attempts + 1,
            next_attempt_at=next_attempt_at,
            result=(detail or "Retry")[:200],
        )
    )


def mark_failed(
    db: Session,
    log_id: int,
    detail: str,
) -> None:
    """Updates log row to terminal failed state after exhausted attempts or non-retryable error."""
    db.execute(
        update(NotificationLog)
        .where(NotificationLog.id == log_id)
        .values(
            status="failed",
            attempts=NotificationLog.attempts + 1,
            result=(detail or "Failed")[:200],
        )
    )


def purge_old(db: Session, now: datetime.datetime) -> int:
    """Prunes terminal notification records (sent, failed, skipped) older than retention days."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    cutoff = now - datetime.timedelta(days=NOTIFY_LOG_RETENTION_DAYS)
    result = db.execute(
        delete(NotificationLog).where(
            NotificationLog.status.in_(["sent", "failed", "skipped"]),
            NotificationLog.created_at < cutoff,
        )
    )
    return result.rowcount or 0
