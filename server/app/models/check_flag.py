"""Shift check-out exception flag database entity model.

Owns the check_flags table schema, unclosed shift review records, and clearance tracking.
Must NOT handle HTTP requests, calculate overdue status, or trigger background cycles directly.
"""
from sqlalchemy import (
    BigInteger,
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


class CheckFlag(Base):
    """Missing check-out flag for an ended worker shift."""
    __tablename__ = "check_flags"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assignment_id = Column(
        BigInteger,
        ForeignKey("assignments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    raised_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    cleared_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cleared_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(500), nullable=True)

    __table_args__ = (
        Index(
            "ix_check_flags_raised_at",
            "raised_at",
            postgresql_where=text("cleared_at IS NULL"),
        ),
    )

    user = relationship("User", foreign_keys=[user_id])
    assignment = relationship("Assignment", foreign_keys=[assignment_id])
    clearer = relationship("User", foreign_keys=[cleared_by])
