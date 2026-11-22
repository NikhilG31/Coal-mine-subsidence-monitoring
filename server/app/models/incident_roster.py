"""Emergency incident worker roll call snapshot database entity model.

Owns the incident_roster table schema, worker snapshots, and evacuation accounting.
Must NOT handle HTTP requests, perform gate scans, or mutate incident states directly.
"""
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class IncidentRoster(Base):
    """Snapshot worker roll call record for an emergency evacuation incident."""
    __tablename__ = "incident_roster"

    incident_id = Column(BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    accounted_at = Column(DateTime(timezone=True), nullable=True)
    accounted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    incident = relationship("Incident", back_populates="roster")
    user = relationship("User", foreign_keys=[user_id])
    accounted_user = relationship("User", foreign_keys=[accounted_by])
