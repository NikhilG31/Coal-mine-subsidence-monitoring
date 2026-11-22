"""QR badge token database entity model.

Owns the qr_tokens table schema, token hash storage, and active badge uniqueness.
Must NOT handle HTTP requests, issue SVG QR codes, or store plaintext tokens.
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


class QRToken(Base):
    """Worker QR badge authorization token."""
    __tablename__ = "qr_tokens"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    issued_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=text("now()"),
    )
    issued_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index(
            "uq_qr_tokens_one_active",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user = relationship("User", foreign_keys=[user_id], back_populates="qr_tokens")
    issuer = relationship("User", foreign_keys=[issued_by])
    revoker = relationship("User", foreign_keys=[revoked_by])
