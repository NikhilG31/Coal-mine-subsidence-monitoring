"""Worker profile database entity model.

Owns the worker_profiles table schema for mine workers (foremen and miners).
Must NOT serialize HTTP responses, validate request bodies, or calculate work shifts.
"""
from sqlalchemy import Column, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class WorkerProfile(Base):
    """Personal and emergency contact profile for a worker (foreman/miner)."""
    __tablename__ = "worker_profiles"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_no = Column(String(64), nullable=False)
    phone = Column(String(32), nullable=True)
    emergency_contact_name = Column(String(255), nullable=True)
    emergency_contact_phone = Column(String(32), nullable=True)
    joined_on = Column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint("company_id", "employee_no", name="uq_worker_profiles_company_employee_no"),
    )

    user = relationship("User", back_populates="worker_profile")
    company = relationship("Company")
