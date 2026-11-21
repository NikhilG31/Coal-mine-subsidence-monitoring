"""Gateway database entity model.

Owns the gateways table schema, hardware MAC indexing, and mine site binding.
Must NOT handle HTTP packet ingestion, unpack binary structs, or verify raw API keys.
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, true
from sqlalchemy.orm import relationship

from app.database import Base


class Gateway(Base):
    """Hardware ESP32 gateway forwarding packets from ESP-NOW to server."""
    __tablename__ = "gateways"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mac = Column(String(32), unique=True, nullable=False, index=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    api_key_hash = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default=true())
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    mine = relationship("Mine", back_populates="gateways")
    creator = relationship("User", foreign_keys=[created_by])
    nodes = relationship("Node", back_populates="gateway", cascade="all, delete-orphan")
