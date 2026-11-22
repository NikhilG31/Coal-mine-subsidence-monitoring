"""Subsidence and connectivity alert database entity model.

Owns the alerts table schema, partial unique open-alert constraints, and acknowledgement fields.
Must NOT evaluate offline timeouts or dispatch outbound external notifications.
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
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Alert(Base):
    """System connectivity and hazard alerts with single-open-alert guarantees."""
    __tablename__ = "alerts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    gateway_id = Column(Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=True, index=True)
    kind = Column(String(32), nullable=False)
    title = Column(String(255), nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('node_offline', 'gateway_offline')",
            name="ck_alerts_kind",
        ),
        CheckConstraint(
            "(kind = 'node_offline' AND node_id IS NOT NULL) OR (kind = 'gateway_offline' AND node_id IS NULL)",
            name="ck_alerts_kind_node",
        ),
        Index(
            "uq_alerts_open_node",
            "node_id",
            unique=True,
            postgresql_where=text("kind = 'node_offline' AND resolved_at IS NULL"),
        ),
        Index(
            "uq_alerts_open_gateway",
            "gateway_id",
            unique=True,
            postgresql_where=text("kind = 'gateway_offline' AND resolved_at IS NULL"),
        ),
        Index("ix_alerts_mine_id_opened_at", "mine_id", text("opened_at DESC")),
    )

    company = relationship("Company")
    mine = relationship("Mine")
    gateway = relationship("Gateway")
    node = relationship("Node")
    acknowledged_by_user = relationship("User", foreign_keys=[acknowledged_by])
