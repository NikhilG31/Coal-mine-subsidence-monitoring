"""Service for node placement state, predicates, and query expressions.

Owns the single source of truth for node placement status and placement integrity rules.
Must NOT execute database migrations or handle HTTP request parsing directly.
"""
from typing import Any
from sqlalchemy import and_
from app.models.node import Node


def is_placed(node: Any) -> bool:
    """Returns True if and only if tunnel_id, lat, and lon are all set."""
    return (
        getattr(node, "tunnel_id", None) is not None
        and getattr(node, "lat", None) is not None
        and getattr(node, "lon", None) is not None
    )


def is_unplaced(node: Any) -> bool:
    """Returns True if and only if tunnel_id, lat, and lon are all None."""
    return (
        getattr(node, "tunnel_id", None) is None
        and getattr(node, "lat", None) is None
        and getattr(node, "lon", None) is None
    )


# Single source of truth SQL expressions for database queries
PLACED_SQL_EXPRESSION = and_(
    Node.tunnel_id.isnot(None),
    Node.lat.isnot(None),
    Node.lon.isnot(None),
)

UNPLACED_SQL_EXPRESSION = and_(
    Node.tunnel_id.is_(None),
    Node.lat.is_(None),
    Node.lon.is_(None),
)
