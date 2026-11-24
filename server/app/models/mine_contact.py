"""Mine emergency key contact database entity model.

Owns the mine_contacts table schema, role validation, emergency call hierarchy rank ordering.
Must NOT initiate external calls, handle HTTP requests, or bypass mine tenant scoping.
"""
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class MineContact(Base):
    """Key contact person for a coal mine emergency call hierarchy."""
    __tablename__ = "mine_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(32), nullable=False)
    rank = Column(SmallInteger, nullable=False)

    mine = relationship("Mine", back_populates="contacts")

    __table_args__ = (
        CheckConstraint(
            "role IN ('shift_supervisor', 'safety_officer', 'rescue_station', 'other')",
            name="ck_mine_contacts_role",
        ),
        CheckConstraint("rank BETWEEN 1 AND 20", name="ck_mine_contacts_rank"),
        UniqueConstraint("mine_id", "rank", name="uq_mine_contacts_rank"),
    )
