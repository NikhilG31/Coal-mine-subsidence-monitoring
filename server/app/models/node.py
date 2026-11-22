"""Sensor node database entity model.

Owns the nodes table schema, hardware identifier uniqueness, and surface/underground placement.
Must NOT serialize JSON responses, validate HTTP inputs, or execute command dispatches.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, SmallInteger, String, UniqueConstraint, false, text
from sqlalchemy.orm import relationship

from app.database import Base


class Node(Base):
    """Sensor node. If tunnel_id is NULL, the node is 'unplaced'."""
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_id = Column(Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False, index=True)
    node_no = Column(SmallInteger, nullable=False)  # 1..255
    mac = Column(String(32), nullable=True)
    tunnel_id = Column(Integer, ForeignKey("tunnels.id", ondelete="SET NULL"), nullable=True, index=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    placed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    placed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="normal", server_default=text("'normal'"))
    battery_pct = Column(Float, nullable=True)
    fast_mode = Column(Boolean, nullable=False, default=False, server_default=false())
    buzzer_on = Column(Boolean, nullable=False, default=False, server_default=false())
    last_cmd_seq = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("gateway_id", "node_no", name="uq_nodes_gateway_node_no"),
        CheckConstraint("node_no BETWEEN 1 AND 255", name="ck_nodes_node_no_range"),
        CheckConstraint(
            "(tunnel_id IS NULL AND lat IS NULL AND lon IS NULL) OR "
            "(tunnel_id IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL)",
            name="ck_nodes_placement_complete",
        ),
    )

    gateway = relationship("Gateway", back_populates="nodes")
    tunnel = relationship("Tunnel", back_populates="nodes")
    placer = relationship("User", foreign_keys=[placed_by])
    readings = relationship("Reading", back_populates="node", cascade="all, delete-orphan")
