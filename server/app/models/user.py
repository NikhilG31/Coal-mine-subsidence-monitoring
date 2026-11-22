"""User and UserMine database entity models.

Owns the users table and user_mines many-to-many relationship mapping permitted mines.
Must NOT hash passwords, issue JWT tokens, or evaluate route permission policies.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, String, Text, false, text, true
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    """System users with role-based access control."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    active = Column(Boolean, nullable=False, default=True, server_default=true())
    force_pw_change = Column(Boolean, nullable=False, default=False, server_default=false())
    token_version = Column(Integer, nullable=False, default=0, server_default=text("0"))

    __table_args__ = (
        CheckConstraint(
            "role IN ('manager', 'safety_officer', 'engineer', 'foreman', 'miner')",
            name="ck_users_role",
        ),
    )

    company = relationship("Company", back_populates="users")
    mine_associations = relationship("UserMine", back_populates="user", cascade="all, delete-orphan")
    worker_profile = relationship("WorkerProfile", uselist=False, back_populates="user", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="user", foreign_keys="Assignment.user_id", cascade="all, delete-orphan")
    qr_tokens = relationship("QRToken", back_populates="user", foreign_keys="QRToken.user_id", cascade="all, delete-orphan")


class UserMine(Base):
    """Association table mapping users to permitted mines."""
    __tablename__ = "user_mines"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), primary_key=True)

    user = relationship("User", back_populates="mine_associations")
    mine = relationship("Mine", back_populates="user_associations")
