"""Sensor node downlink hardware radio command entity model.

Owns the node_commands table schema, sequence tracking, and acknowledgement records.
Must NOT format binary frames, handle HTTP requests, or manage transaction rollbacks.
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


class NodeCommand(Base):
    """Downlink hardware command dispatched to an underground sensor node."""
    __tablename__ = "node_commands"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    cmd = Column(String(12), nullable=False)
    seq = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    acked_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    node = relationship("Node", backref="commands")
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        CheckConstraint("cmd IN ('buzzer_on', 'buzzer_off', 'resume')", name="ck_node_commands_cmd"),
        CheckConstraint("seq BETWEEN 1 AND 65535", name="ck_node_commands_seq"),
        Index("ix_node_commands_node_id_created_at", "node_id", text("created_at DESC")),
        Index("ix_node_commands_pending", "node_id", postgresql_where=text("acked_at IS NULL")),
    )
