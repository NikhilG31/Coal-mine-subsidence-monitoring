"""In-memory rate limiting for login attempts.

Owns tracking consecutive authentication failures per (username, client IP) in-memory.
Documented limitation: single-process memory only; does not share state across cluster instances.
Must NOT interact with database sessions or handle HTTP routing.
"""
import threading
import time
from typing import Dict, List, Optional, Tuple

MAX_FAILURES: int = 5
WINDOW_SECONDS: int = 15 * 60  # 15 minutes

_lock = threading.Lock()
# Key: (username.lower(), ip) -> list of failure timestamps
_failures: Dict[Tuple[str, str], List[float]] = {}


def check_login_rate_limit(username: str, client_ip: str) -> Optional[int]:
    """Checks whether the client has exceeded login failure attempts.

    Returns:
        Retry-After seconds if rate limited, or None if request is permitted.
    """
    key = (username.strip().lower(), client_ip.strip())
    now = time.time()
    with _lock:
        timestamps = _failures.get(key, [])
        # Filter timestamps within current sliding window
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


def clear_all_rate_limits() -> None:
    """Clears all in-memory rate limit counters (used by test suites)."""
    with _lock:
        _failures.clear()
