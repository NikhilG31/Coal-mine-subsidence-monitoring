"""Emergency and evacuation incident database entity model.

Owns the incidents table schema, lifecycle states, siren flags, and clearance records.
Must NOT handle HTTP requests, snapshot worker rosters, or issue hardware commands.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Incident(Base):
    """Underground emergency or manual evacuation incident entity."""
    __tablename__ = "incidents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    tunnel_id = Column(Integer, ForeignKey("tunnels.id", ondelete="RESTRICT"), nullable=True, index=True)
    source = Column(String(8), nullable=False)
    level = Column(SmallInteger, nullable=False)
    title = Column(String(120), nullable=False)
    status = Column(String(8), nullable=False, default="open", server_default=text("'open'"))
    siren_active = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    opened_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    opened_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    close_note = Column(String(500), nullable=True)

    mine = relationship("Mine", backref="incidents")
    tunnel = relationship("Tunnel", backref="incidents")
    opener = relationship("User", foreign_keys=[opened_by])
    closer = relationship("User", foreign_keys=[closed_by])
    roster = relationship("IncidentRoster", back_populates="incident", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("source IN ('auto', 'manual', 'sos')", name="ck_incidents_source"),
        CheckConstraint("level BETWEEN 1 AND 3", name="ck_incidents_level"),
        CheckConstraint("status IN ('open', 'closed')", name="ck_incidents_status"),
        CheckConstraint(
            "((status = 'closed') = (closed_at IS NOT NULL))",
            name="ck_incidents_closed_status",
        ),
        Index(
            "uq_incidents_open_auto",
            "tunnel_id",
            unique=True,
            postgresql_where=text("status = 'open' AND source = 'auto'"),
        ),
        Index(
            "uq_incidents_open_sos",
            "tunnel_id",
            unique=True,
            postgresql_where=text("status = 'open' AND source = 'sos'"),
        ),
        Index(
            "ix_incidents_mine_id_opened_at",
            "mine_id",
            text("opened_at DESC"),
        ),
    )
