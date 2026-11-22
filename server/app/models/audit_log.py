"""Audit log database entity model.

Owns the audit_log table schema and chronological security event recording.
Must NOT redact sensitive fields or evaluate role permissions.
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class AuditLog(Base):
    """Immutable audit trail for all operational mutations and security actions."""
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(64), nullable=False)
    entity = Column(String(64), nullable=False)
    entity_id = Column(Integer, nullable=True)
    detail = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        Index("ix_audit_log_company_id_ts", "company_id", text("ts DESC")),
    )

    company = relationship("Company")
    user = relationship("User")
