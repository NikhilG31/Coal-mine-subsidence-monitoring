"""Notification channel settings and alert preference database entity model.

Owns the notification_settings table schema, per-mine delivery toggles, and destinations.
Must NOT execute HTTP calls, dispatch alerts, or validate external secrets directly.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class NotificationSettings(Base):
    """Mine-level notification channel configurations and event toggles."""
    __tablename__ = "notification_settings"

    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), primary_key=True)
    telegram_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    telegram_chat_id = Column(String(64), nullable=True)
    whatsapp_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    whatsapp_to = Column(String(20), nullable=True)
    webhook_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    webhook_url = Column(String(500), nullable=True)

    on_incident_open = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    on_incident_close = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    on_sos = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    on_offline = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )

    mine = relationship("Mine", backref="notification_settings", uselist=False)
    updater = relationship("User", foreign_keys=[updated_by])

    __table_args__ = (
        CheckConstraint(
            "(NOT telegram_enabled) OR (telegram_chat_id IS NOT NULL)",
            name="ck_notification_settings_telegram",
        ),
        CheckConstraint(
            "(NOT whatsapp_enabled) OR (whatsapp_to IS NOT NULL)",
            name="ck_notification_settings_whatsapp",
        ),
        CheckConstraint(
            "(NOT webhook_enabled) OR (webhook_url IS NOT NULL)",
            name="ck_notification_settings_webhook",
        ),
    )
