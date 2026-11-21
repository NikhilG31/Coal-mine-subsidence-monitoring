"""SQLAlchemy database models for Coal Mine Subsidence Monitoring System (Step 1).

Tables:
- companies
- mines
- tunnels
- users
- user_mines
- gateways
- nodes
- readings
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


def normalize_company_name(name: str) -> str:
    """Lowercase, trimmed, internal whitespace collapsed."""
    return " ".join((name or "").strip().lower().split())


class Company(Base):
    """Company owning mines. Unique normalized name prevents casing duplicates."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    name_normalized = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    mines = relationship("Mine", back_populates="company", cascade="all, delete-orphan")
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")


class Mine(Base):
    """A mine site operated by a company."""
    __tablename__ = "mines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    subsidiary = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    method = Column(String(32), nullable=False, default="underground")
    area_ha = Column(Float, nullable=True)
    max_depth_m = Column(Float, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'maintenance', 'suspended', 'closed')", name="ck_mines_status"),
        CheckConstraint("method IN ('opencast', 'underground', 'mixed')", name="ck_mines_method"),
    )

    company = relationship("Company", back_populates="mines")
    tunnels = relationship("Tunnel", back_populates="mine", cascade="all, delete-orphan")
    gateways = relationship("Gateway", back_populates="mine", cascade="all, delete-orphan")
    user_associations = relationship("UserMine", back_populates="mine", cascade="all, delete-orphan")


class Tunnel(Base):
    """An underground tunnel / gallery / incline within a mine."""
    __tablename__ = "tunnels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(64), nullable=False)
    depth_m = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="active")

    __table_args__ = (
        UniqueConstraint("mine_id", "code", name="uq_tunnels_mine_code"),
    )

    mine = relationship("Mine", back_populates="tunnels")
    nodes = relationship("Node", back_populates="tunnel")


class User(Base):
    """System users with role-based access control."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    force_pw_change = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('manager', 'safety_officer', 'engineer', 'foreman', 'miner')",
            name="ck_users_role",
        ),
    )

    company = relationship("Company", back_populates="users")
    mine_associations = relationship("UserMine", back_populates="user", cascade="all, delete-orphan")


class UserMine(Base):
    """Association table mapping users to permitted mines."""
    __tablename__ = "user_mines"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), primary_key=True)

    user = relationship("User", back_populates="mine_associations")
    mine = relationship("Mine", back_populates="user_associations")


class Gateway(Base):
    """Hardware ESP32 gateway forwarding packets from ESP-NOW to server."""
    __tablename__ = "gateways"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mac = Column(String(32), unique=True, nullable=False, index=True)
    mine_id = Column(Integer, ForeignKey("mines.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    api_key_hash = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    mine = relationship("Mine", back_populates="gateways")
    creator = relationship("User", foreign_keys=[created_by])
    nodes = relationship("Node", back_populates="gateway", cascade="all, delete-orphan")


class Node(Base):
    """Sensor node. If tunnel_id is NULL, the node is 'unplaced'."""
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_id = Column(Integer, ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False, index=True)
    node_no = Column(SmallInteger, nullable=False)  # 1..255
    mac = Column(String(32), nullable=True)
    tunnel_id = Column(Integer, ForeignKey("tunnels.id", ondelete="SET NULL"), nullable=True, index=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    placed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    placed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="normal")
    battery_pct = Column(Float, nullable=True)
    fast_mode = Column(Boolean, nullable=False, default=False)
    buzzer_on = Column(Boolean, nullable=False, default=False)
    last_cmd_seq = Column(Integer, nullable=False, default=0)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("gateway_id", "node_no", name="uq_nodes_gateway_node_no"),
    )

    gateway = relationship("Gateway", back_populates="nodes")
    tunnel = relationship("Tunnel", back_populates="nodes")
    placer = relationship("User", foreign_keys=[placed_by])
    readings = relationship("Reading", back_populates="node", cascade="all, delete-orphan")


class Reading(Base):
    """Individual telemetry reading recorded from a sensor node."""
    __tablename__ = "readings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    boot_id = Column(BigInteger, nullable=False, default=0)
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
