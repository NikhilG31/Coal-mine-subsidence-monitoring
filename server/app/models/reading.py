"""Telemetry reading database entity model.

Owns the readings time-series table schema and deduplication indexes.
Must NOT evaluate hazard thresholds, decode binary packets, or format export files.
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, text
from sqlalchemy.orm import relationship

from app.database import Base


class Reading(Base):
    """Individual telemetry reading recorded from a sensor node."""
    __tablename__ = "readings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    boot_id = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    counter = Column(BigInteger, nullable=False)
    tilt_x = Column(Float, nullable=False)
    tilt_y = Column(Float, nullable=False)
    vib_rms = Column(Float, nullable=False)
    shock = Column(Boolean, nullable=False)
    batt_v = Column(Float, nullable=False)
    batt_pct = Column(Float, nullable=False)
    node_level = Column(SmallInteger, nullable=False)
    level = Column(SmallInteger, nullable=False)

    __table_args__ = (
        Index("ix_readings_node_id_ts", "node_id", text("ts DESC")),
        Index(
            "uq_readings_node_boot_counter",
            "node_id",
            "boot_id",
            "counter",
            unique=True,
            postgresql_where=text("boot_id <> 0"),
        ),
    )

    node = relationship("Node", back_populates="readings")
