"""Worker safety report and device SOS database entity model.

Owns the worker_reports table schema, lifecycle states, repeat tracking, and resolution details.
Must NOT evaluate hazard thresholds, handle HTTP requests, or issue hardware commands.
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


class WorkerReport(Base):
    """Worker hazard/incident report or hardware node device SOS signal."""
    __tablename__ = "worker_reports"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    tunnel_id = Column(Integer, ForeignKey("tunnels.id", ondelete="RESTRICT"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True)
    source = Column(String(6), nullable=False)
    type = Column(String(16), nullable=False)
    severity = Column(String(8), nullable=False)
    description = Column(String(1000), nullable=True)
    status = Column(String(12), nullable=False, default="open", server_default=text("'open'"))
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    last_signal_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    repeat_count = Column(Integer, nullable=False, default=1, server_default=text("1"))
    acknowledged_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(String(500), nullable=True)
    incident_id = Column(BigInteger, ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)

    mine = relationship("Mine", backref="worker_reports")
    tunnel = relationship("Tunnel", backref="worker_reports")
    reporter = relationship("User", foreign_keys=[user_id])
    node = relationship("Node", backref="worker_reports")
    acknowledger = relationship("User", foreign_keys=[acknowledged_by])
    resolver = relationship("User", foreign_keys=[resolved_by])
    incident = relationship("Incident", backref="worker_reports")

    __table_args__ = (
        CheckConstraint("source IN ('worker', 'device')", name="ck_reports_source"),
        CheckConstraint("type IN ('injury', 'equipment_fault', 'gas_smell', 'need_help', 'sos')", name="ck_reports_type"),
        CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name="ck_reports_severity"),
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="ck_reports_status"),
        CheckConstraint("repeat_count >= 1", name="ck_reports_repeat_count"),
        CheckConstraint(
            "(source = 'device' AND node_id IS NOT NULL AND user_id IS NULL AND type = 'sos') OR (source = 'worker')",
            name="ck_reports_source_shape",
        ),
        CheckConstraint(
            "(((status = 'open') AND (acknowledged_at IS NULL) AND (resolved_at IS NULL)) OR "
            "((status = 'acknowledged') AND (acknowledged_at IS NOT NULL) AND (resolved_at IS NULL)) OR "
            "((status = 'resolved') AND (acknowledged_at IS NOT NULL) AND (resolved_at IS NOT NULL)))",
            name="ck_reports_status_fields",
        ),
        CheckConstraint("(type <> 'sos' OR severity = 'critical')", name="ck_reports_sos_critical"),
        Index("ix_worker_reports_mine_status_created", "mine_id", "status", text("created_at DESC")),
        Index(
            "ix_worker_reports_user_created",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_worker_reports_node_signal",
            "node_id",
            text("last_signal_at DESC"),
            postgresql_where=text("source = 'device'"),
        ),
    )
