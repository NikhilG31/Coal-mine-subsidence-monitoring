"""Notification outbox and delivery audit log database entity model.

Owns the notification_log table schema, delivery attempt tracking, and deduplication constraints.
Must NOT execute network I/O, format messages, or manage delivery workers directly.
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class NotificationLog(Base):
    """Transactional outbox and delivery history for external notification dispatches."""
    __tablename__ = "notification_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False)
    event = Column(String(20), nullable=False)
    ref_type = Column(String(16), nullable=True)
    ref_id = Column(BigInteger, nullable=True)
    incident_id = Column(BigInteger, ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String(10), nullable=False)
    status = Column(String(10), nullable=False, default="pending", server_default=text("'pending'"))
    attempts = Column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    next_attempt_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(String(200), nullable=True)
    message = Column(String(1000), nullable=False)
    dedupe_key = Column(String(120), nullable=False)

    mine = relationship("Mine", backref="notification_logs")
    incident = relationship("Incident", backref="notification_logs")

    __table_args__ = (
        UniqueConstraint("dedupe_key", "channel", name="uq_notification_dedupe"),
        CheckConstraint(
            "event IN ('incident_open', 'incident_close', 'sos_report', 'offline_alert', 'test')",
            name="ck_notification_log_event",
        ),
        CheckConstraint(
            "channel IN ('telegram', 'whatsapp', 'webhook')",
            name="ck_notification_log_channel",
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped')",
            name="ck_notification_log_status",
        ),
        CheckConstraint(
            "((status = 'sent' AND sent_at IS NOT NULL) OR (status <> 'sent' AND sent_at IS NULL))",
            name="ck_notification_log_sent_at",
        ),
        Index(
            "ix_notification_log_pending",
            "status",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_notification_log_mine_created",
            "mine_id",
            text("created_at DESC"),
        ),
    )
