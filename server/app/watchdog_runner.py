"""Background watchdog runner daemon for gateway and node offline detection.

Owns background thread execution, startup grace period delays, and advisory transaction lock coordination.
Must NOT handle HTTP requests, modify database schemas, or send external notifications.
"""
import datetime
import logging
import threading
import time
from typing import Dict, Optional

from sqlalchemy import text

from app.config import (
    NODE_OFFLINE_AFTER_S,
    WATCHDOG_ENABLED,
    WATCHDOG_INTERVAL_S,
)
from app.database import SessionLocal
from app.services.watchdog_service import run_watchdog_cycle

logger = logging.getLogger(__name__)

WATCHDOG_LOCK_KEY = 8392104


class WatchdogRunner:
    """Manages the periodic execution of the connectivity watchdog."""

    def __init__(
        self,
        interval_s: int = WATCHDOG_INTERVAL_S,
        grace_period_s: int = NODE_OFFLINE_AFTER_S,
        lock_key: int = WATCHDOG_LOCK_KEY,
    ):
        self.interval_s = interval_s
        self.grace_period_s = grace_period_s
        self.lock_key = lock_key
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.started_at: Optional[float] = None

    def in_grace_period(self) -> bool:
        """Returns True if the startup grace period is currently active."""
        if self.started_at is None:
            return False
        return (time.monotonic() - self.started_at) < self.grace_period_s

    def run_once(self, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, int]]:
        """Executes a single watchdog cycle under the PostgreSQL advisory transaction lock.

        Skips execution if within the startup grace period or if the advisory lock cannot be acquired.
        """
        if self.in_grace_period():
            elapsed = time.monotonic() - (self.started_at or 0.0)
            logger.debug(
                "Watchdog in startup grace period (%0.1fs / %0.1fs), skipping cycle",
                elapsed,
                self.grace_period_s,
            )
            return None

        db = SessionLocal()
        try:
            if db.bind and db.bind.dialect.name == "postgresql":
                locked = db.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": self.lock_key},
                ).scalar()
                if not locked:
                    db.rollback()
                    logger.debug("Watchdog advisory lock not acquired, skipping cycle")
                    return None

            cycle_now = now or datetime.datetime.now(datetime.timezone.utc)
            result = run_watchdog_cycle(db, cycle_now)
            logger.debug("Watchdog cycle completed: %s", result)
            return result
        except Exception as exc:
            db.rollback()
            logger.exception("Watchdog cycle error: %s", exc)
            return None
        finally:
            db.close()

    def _loop(self) -> None:
        """Loop running until stop_event is set."""
        logger.info("Watchdog runner daemon thread started (interval: %ds).", self.interval_s)
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.exception("Unexpected error in watchdog runner loop: %s", exc)

            # Wait for interval or stop_event
            self.stop_event.wait(timeout=self.interval_s)
        logger.info("Watchdog runner daemon thread stopped.")

    def start(self) -> None:
        """Starts the daemon thread if enabled."""
        if not WATCHDOG_ENABLED:
            logger.info("Watchdog is disabled by configuration (WATCHDOG_ENABLED=False).")
            return

        if self.thread and self.thread.is_alive():
            logger.warning("Watchdog runner is already running.")
            return

        self.stop_event.clear()
        self.started_at = time.monotonic()
        self.thread = threading.Thread(target=self._loop, name="watchdog-runner", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Signals the daemon thread to stop and waits for it to exit."""
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            self.thread = None


runner = WatchdogRunner()
