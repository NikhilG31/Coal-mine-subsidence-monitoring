"""Daily coal mine production plan and actual output database entity model.

Owns the mine_production table schema, daily tonnage tracking, and update attribution.
Must NOT perform monthly aggregation, handle HTTP requests, or format API responses.
"""
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class MineProduction(Base):
    """Daily coal extraction tonnage plan vs actual output record for a mine."""
    __tablename__ = "mine_production"

    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    day = Column(Date, primary_key=True, nullable=False)
    planned_t = Column(Numeric(12, 2), nullable=True)
    actual_t = Column(Numeric(12, 2), nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )

    mine = relationship("Mine", back_populates="production_entries")
    updater = relationship("User", foreign_keys=[updated_by])

    __table_args__ = (
        CheckConstraint("planned_t >= 0", name="ck_mine_production_planned_t"),
        CheckConstraint("actual_t >= 0", name="ck_mine_production_actual_t"),
        CheckConstraint(
            "planned_t IS NOT NULL OR actual_t IS NOT NULL",
            name="ck_mine_production_at_least_one",
        ),
    )
