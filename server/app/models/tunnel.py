"""Tunnel database entity model.

Owns the tunnels table schema and unique mine-code constraints for underground workings.
Must NOT track shift assignments, calculate hazard classifications, or parse packets.
"""
from sqlalchemy import Column, Float, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import relationship

from app.database import Base


class Tunnel(Base):
    """An underground tunnel / gallery / incline within a mine."""
    __tablename__ = "tunnels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(64), nullable=False)
    depth_m = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default=text("'active'"))

    __table_args__ = (
        UniqueConstraint("mine_id", "code", name="uq_tunnels_mine_code"),
    )

    mine = relationship("Mine", back_populates="tunnels")
    nodes = relationship("Node", back_populates="tunnel")
