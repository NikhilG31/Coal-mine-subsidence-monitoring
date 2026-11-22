"""Worker shift assignment database entity model.

Owns the assignments table schema, time range validation, and overlap exclusion constraints.
Must NOT serialize HTTP responses, perform scheduling arithmetic, or enforce route policies.
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class Assignment(Base):
    """Underground tunnel shift assignment for a worker."""
    __tablename__ = "assignments"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tunnel_id = Column(Integer, ForeignKey("tunnels.id", ondelete="RESTRICT"), nullable=False)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_assignments_range"),
        ExcludeConstraint(
            ("user_id", "="),
            (func.tstzrange(text("starts_at"), text("ends_at"), "[)"), "&&"),
            using="gist",
            name="ex_assignments_user_no_overlap",
        ),
        Index("ix_assignments_user_id_starts_at", "user_id", text("starts_at DESC")),
        Index("ix_assignments_tunnel_id_starts_at", "tunnel_id", "starts_at"),
    )

    user = relationship("User", foreign_keys=[user_id], back_populates="assignments")
    tunnel = relationship("Tunnel", foreign_keys=[tunnel_id])
    creator = relationship("User", foreign_keys=[created_by])
