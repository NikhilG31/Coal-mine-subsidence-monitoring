"""In-memory rate limiting for login and gate scanning attempts.

Owns tracking consecutive authentication failures and scan throughput in-memory.
Documented limitation: single-process memory only; does not share state across cluster instances.
Must NOT interact with database sessions or handle HTTP routing.
"""
import threading
import time
from typing import Dict, List, Optional, Tuple

from app.config import SCAN_RATE_PER_MIN

MAX_FAILURES: int = 5
WINDOW_SECONDS: int = 15 * 60  # 15 minutes
SCAN_WINDOW_SECONDS: float = 60.0
UNKNOWN_BADGE_MAX: int = 10

_lock = threading.Lock()
# Key: (username.lower(), ip) -> list of failure timestamps
_failures: Dict[Tuple[str, str], List[float]] = {}
# Scanner user_id -> list of scan attempt timestamps
_scan_attempts: Dict[int, List[float]] = {}
# Scanner user_id -> list of unknown-badge failure timestamps
_unknown_badge_failures: Dict[int, List[float]] = {}


def check_login_rate_limit(username: str, client_ip: str) -> Optional[int]:
    """Checks whether the client has exceeded login failure attempts.

    Returns:
        Retry-After seconds if rate limited, or None if request is permitted.
    """
    key = (username.strip().lower(), client_ip.strip())
    now = time.time()
    with _lock:
        timestamps = _failures.get(key, [])
        valid = [t for t in timestamps if now - t < WINDOW_SECONDS]
        _failures[key] = valid

        if len(valid) >= MAX_FAILURES:
            oldest_in_window = valid[0]
            retry_after = max(1, int(WINDOW_SECONDS - (now - oldest_in_window)))
            return retry_after
        return None


def record_login_failure(username: str, client_ip: str) -> None:
    """Records an authentication failure timestamp for the (username, client IP) pair."""
    key = (username.strip().lower(), client_ip.strip())
    now = time.time()
    with _lock:
        timestamps = _failures.get(key, [])
        valid = [t for t in timestamps if now - t < WINDOW_SECONDS]
        valid.append(now)
        _failures[key] = valid


def record_login_success(username: str, client_ip: str) -> None:
    """Clears failure history upon successful authentication."""
    key = (username.strip().lower(), client_ip.strip())
    with _lock:
        if key in _failures:
            del _failures[key]


def check_scan_rate_limit(user_id: int) -> Optional[int]:
    """Checks whether scanner user has exceeded SCAN_RATE_PER_MIN.

    Returns:
        Retry-After seconds if rate limited, or None if request is permitted.
    """
    now = time.time()
    with _lock:
        timestamps = _scan_attempts.get(user_id, [])
        valid = [t for t in timestamps if now - t < SCAN_WINDOW_SECONDS]
        _scan_attempts[user_id] = valid

        if len(valid) >= SCAN_RATE_PER_MIN:
            oldest = valid[0]
            return max(1, int(SCAN_WINDOW_SECONDS - (now - oldest)))
        return None


def record_scan_attempt(user_id: int) -> None:
    """Records a gate scan attempt timestamp for the scanner user."""
    now = time.time()
    with _lock:
        timestamps = _scan_attempts.get(user_id, [])
        valid = [t for t in timestamps if now - t < SCAN_WINDOW_SECONDS]
        valid.append(now)
        _scan_attempts[user_id] = valid


def check_unknown_badge_rate_limit(user_id: int) -> Optional[int]:
    """Checks whether scanner user has exceeded 10 unknown badge attempts per minute.

    Returns:
        Retry-After seconds if rate limited, or None if request is permitted.
    """
    now = time.time()
    with _lock:
        timestamps = _unknown_badge_failures.get(user_id, [])
        valid = [t for t in timestamps if now - t < SCAN_WINDOW_SECONDS]
        _unknown_badge_failures[user_id] = valid

        if len(valid) >= UNKNOWN_BADGE_MAX:
            oldest = valid[0]
            return max(1, int(SCAN_WINDOW_SECONDS - (now - oldest)))
        return None


def record_unknown_badge_failure(user_id: int) -> None:
    """Records an unknown badge scan failure timestamp for the scanner user."""
    now = time.time()
    with _lock:
        timestamps = _unknown_badge_failures.get(user_id, [])
        valid = [t for t in timestamps if now - t < SCAN_WINDOW_SECONDS]
        valid.append(now)
        _unknown_badge_failures[user_id] = valid


def clear_all_rate_limits() -> None:
    """Clears all in-memory rate limit counters (used by test suites)."""
    with _lock:
        _failures.clear()
        _scan_attempts.clear()
        _unknown_badge_failures.clear()
