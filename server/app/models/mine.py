"""Mine database entity model.

Owns the mines table schema, status and method constraints, and child site associations.
Must NOT execute GIS spatial calculations, telemetry ingest, or authorization checks.
"""
from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import relationship

from app.database import Base


class Mine(Base):
    """A coal mine site operated by a company."""
    __tablename__ = "mines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    subsidiary = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default=text("'active'"))
    method = Column(String(32), nullable=False, default="underground", server_default=text("'underground'"))
    area_ha = Column(Float, nullable=True)
    max_depth_m = Column(Float, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    evacuation_notes = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'maintenance', 'suspended', 'closed')", name="ck_mines_status"),
        CheckConstraint("method IN ('opencast', 'underground', 'mixed')", name="ck_mines_method"),
        CheckConstraint("char_length(evacuation_notes) <= 2000", name="ck_mines_evacuation_notes"),
        UniqueConstraint("company_id", "name", name="uq_mines_company_name"),
    )

    company = relationship("Company", back_populates="mines")
    tunnels = relationship("Tunnel", back_populates="mine", cascade="all, delete-orphan")
    gateways = relationship("Gateway", back_populates="mine", cascade="all, delete-orphan")
    user_associations = relationship("UserMine", back_populates="mine", cascade="all, delete-orphan")
    contacts = relationship("MineContact", back_populates="mine", cascade="all, delete-orphan", order_by="MineContact.rank")
    production_entries = relationship("MineProduction", back_populates="mine", cascade="all, delete-orphan")
