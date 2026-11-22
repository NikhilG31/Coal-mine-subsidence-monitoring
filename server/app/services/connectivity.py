"""Connectivity state determination for nodes and gateways.

Owns online/offline evaluation functions based on last_seen_at timestamps and configured thresholds.
Must NOT execute database queries or import FastAPI routing constructs.
"""
import datetime
from typing import Optional

from app.config import GATEWAY_OFFLINE_AFTER_S, NODE_OFFLINE_AFTER_S


def _to_utc(dt: datetime.datetime) -> datetime.datetime:
    """Ensures a datetime object is timezone-aware in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def node_is_online(
    last_seen_at: Optional[datetime.datetime],
    now: datetime.datetime,
) -> bool:
    """Evaluates whether a sensor node is online.

    Returns True if and only if last_seen_at is not None and
    (now - last_seen_at).total_seconds() <= NODE_OFFLINE_AFTER_S.
    """
    if last_seen_at is None:
        return False
    delta_s = (_to_utc(now) - _to_utc(last_seen_at)).total_seconds()
    return delta_s <= NODE_OFFLINE_AFTER_S


def gateway_is_online(
    last_seen_at: Optional[datetime.datetime],
    now: datetime.datetime,
) -> bool:
    """Evaluates whether a hardware gateway is online.

    Returns True if and only if last_seen_at is not None and
    (now - last_seen_at).total_seconds() <= GATEWAY_OFFLINE_AFTER_S.
    Without hardware heartbeats, a gateway's last_seen_at reflects
    the arrival time of its last accepted packet from any node.
    """
    if last_seen_at is None:
        return False
    delta_s = (_to_utc(now) - _to_utc(last_seen_at)).total_seconds()
    return delta_s <= GATEWAY_OFFLINE_AFTER_S
