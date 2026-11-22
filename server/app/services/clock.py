"""Centralized system clock service.

Owns the single authoritative time provider (utcnow) for all domain operations.
Must NOT use naive datetimes or external NTP/network time sources.
"""
import datetime


def utcnow() -> datetime.datetime:
    """Returns the current timezone-aware UTC datetime."""
    return datetime.datetime.now(datetime.timezone.utc)
