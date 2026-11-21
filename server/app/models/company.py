"""Company database entity model.

Owns the companies table definition, normalized naming constraints, and related cascades.
Must NOT handle HTTP payloads, session transactions, or business logic.
"""
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


def normalize_company_name(name: str) -> str:
    """Normalizes company name: lowercase, stripped, internal whitespace collapsed."""
    return " ".join((name or "").strip().lower().split())


class Company(Base):
    """Company owning mines. Unique normalized name prevents casing duplicates."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    name_normalized = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    mines = relationship("Mine", back_populates="company", cascade="all, delete-orphan")
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")
