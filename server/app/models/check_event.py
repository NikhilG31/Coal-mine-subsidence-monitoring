"""Check-in and check-out event database entity model.

Owns the check_events table schema, scan direction and method constraints, and access history.
Must NOT serialize HTTP responses, perform headcount math, or issue tokens.
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CheckEvent(Base):
    """Underground tunnel check-in or check-out event."""
    __tablename__ = "check_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tunnel_id = Column(Integer, ForeignKey("tunnels.id", ondelete="RESTRICT"), nullable=False)
    direction = Column(String(3), nullable=False)
    method = Column(String(8), nullable=False)
    scanned_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    scanned_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="ck_check_events_direction"),
        CheckConstraint("method IN ('qr', 'manual')", name="ck_check_events_method"),
        Index("ix_check_events_user_id_scanned_at", "user_id", text("scanned_at DESC")),
        Index("ix_check_events_tunnel_id_scanned_at", "tunnel_id", text("scanned_at DESC")),
    )

    user = relationship("User", foreign_keys=[user_id])
    tunnel = relationship("Tunnel", foreign_keys=[tunnel_id])
    scanner = relationship("User", foreign_keys=[scanned_by])
