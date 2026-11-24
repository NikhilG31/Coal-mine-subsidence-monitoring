"""Background runner daemon for connectivity watchdog and shift check flags.

Owns periodic background cycle dispatch, grace period delay, and advisory transaction lock coordination.
Must NOT handle HTTP requests, modify database schemas, or send external notifications.
"""
import datetime
import logging
import threading
import time
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.config import (
    NODE_OFFLINE_AFTER_S,
    SIREN_REFRESH_S,
    WATCHDOG_ENABLED,
    WATCHDOG_INTERVAL_S,
)
from app.database import SessionLocal
from app.services import clock
from app.services.flag_service import run_flag_cycle
from app.services.notification_worker import run_notification_cycle
from app.services.siren_service import run_siren_cycle
from app.services.watchdog_service import run_watchdog_cycle

logger = logging.getLogger(__name__)

WATCHDOG_LOCK_KEY = 8392104
FLAG_LOCK_KEY = 8392105
SIREN_LOCK_KEY = 8392106
NOTIFICATION_LOCK_KEY = 8392107
NOTIFICATION_INTERVAL_S = 5.0


class WatchdogRunner:
    """Manages periodic background execution of watchdog, shift flags, sirens, and notifications."""

    def __init__(
        self,
        interval_s: int = WATCHDOG_INTERVAL_S,
        grace_period_s: int = NODE_OFFLINE_AFTER_S,
        lock_key: int = WATCHDOG_LOCK_KEY,
        flag_lock_key: int = FLAG_LOCK_KEY,
        siren_lock_key: int = SIREN_LOCK_KEY,
        notification_lock_key: int = NOTIFICATION_LOCK_KEY,
    ):
        self.interval_s = interval_s
        self.watchdog_interval_s = float(WATCHDOG_INTERVAL_S)
        self.siren_interval_s = min(10.0, float(SIREN_REFRESH_S) / 3.0)
        self.notification_interval_s = float(NOTIFICATION_INTERVAL_S)
        self.grace_period_s = grace_period_s
        self.lock_key = lock_key
        self.flag_lock_key = flag_lock_key
        self.siren_lock_key = siren_lock_key
        self.notification_lock_key = notification_lock_key
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.started_at: Optional[float] = None

    def in_grace_period(self) -> bool:
        """Returns True if the startup grace period is currently active."""
        if self.started_at is None:
            return False
        return (time.monotonic() - self.started_at) < self.grace_period_s

    def run_watchdog_once(self, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, int]]:
        """Executes a single watchdog cycle under the PostgreSQL advisory transaction lock."""
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

            cycle_now = now or clock.utcnow()
            result = run_watchdog_cycle(db, cycle_now)
            logger.debug("Watchdog cycle completed: %s", result)
            return result
        except Exception as exc:
            db.rollback()
            logger.exception("Watchdog cycle error: %s", exc)
            return None
        finally:
            db.close()

    def run_flag_once(self, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, int]]:
        """Executes a single shift check-flags cycle under its own advisory transaction lock."""
        db = SessionLocal()
        try:
            if db.bind and db.bind.dialect.name == "postgresql":
                locked = db.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": self.flag_lock_key},
                ).scalar()
                if not locked:
                    db.rollback()
                    logger.debug("Flag cycle advisory lock not acquired, skipping cycle")
                    return None

            cycle_now = now or clock.utcnow()
            result = run_flag_cycle(db, cycle_now)
            logger.debug("Flag cycle completed: %s", result)
            return result
        except Exception as exc:
            db.rollback()
            logger.exception("Flag cycle error: %s", exc)
            return None
        finally:
            db.close()

    def run_siren_once(self, now: Optional[datetime.datetime] = None) -> Optional[int]:
        """Executes a single siren refresh cycle under its own advisory transaction lock."""
        db = SessionLocal()
        try:
            if db.bind and db.bind.dialect.name == "postgresql":
                locked = db.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": self.siren_lock_key},
                ).scalar()
                if not locked:
                    db.rollback()
                    logger.debug("Siren cycle advisory lock not acquired, skipping cycle")
                    return None

            cycle_now = now or clock.utcnow()
            result = run_siren_cycle(db, cycle_now)
            db.commit()
            logger.debug("Siren cycle completed: %d refreshed", result)
            return result
        except Exception as exc:
            db.rollback()
            logger.exception("Siren cycle error: %s", exc)
            return None
        finally:
            db.close()

    def run_notification_once(self, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, int]]:
        """Executes a single notification cycle under its own advisory transaction lock."""
        db = SessionLocal()
        try:
            if db.bind and db.bind.dialect.name == "postgresql":
                locked = db.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": self.notification_lock_key},
                ).scalar()
                if not locked:
                    db.rollback()
                    logger.debug("Notification cycle advisory lock not acquired, skipping cycle")
                    return None

            cycle_now = now or clock.utcnow()
            result = run_notification_cycle(db, cycle_now)
            logger.debug("Notification cycle completed: %s", result)
            return result
        except Exception as exc:
            db.rollback()
            logger.exception("Notification cycle error: %s", exc)
            return None
        finally:
            db.close()

    def run_once(self, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
        """Executes watchdog, flag, siren, and notification cycles independently. One failure does not stop others."""
        if self.in_grace_period():
            return None

        watchdog_result = None
        flag_result = None
        siren_result = None
        notification_result = None

        try:
            watchdog_result = self.run_watchdog_once(now)
        except Exception as exc:
            logger.exception("Unexpected error in watchdog execution: %s", exc)

        try:
            flag_result = self.run_flag_once(now)
        except Exception as exc:
            logger.exception("Unexpected error in flag cycle execution: %s", exc)

        try:
            siren_result = self.run_siren_once(now)
        except Exception as exc:
            logger.exception("Unexpected error in siren cycle execution: %s", exc)

        try:
            notification_result = self.run_notification_once(now)
        except Exception as exc:
            logger.exception("Unexpected error in notification cycle execution: %s", exc)

        return {
            "watchdog": watchdog_result,
            "flags": flag_result,
            "siren": siren_result,
            "notifications": notification_result,
        }

    def _loop(self) -> None:
        """Loop running until stop_event is set."""
        logger.info(
            "Watchdog runner daemon thread started (watchdog: %0.1fs, siren: %0.1fs, notifications: %0.1fs).",
            self.watchdog_interval_s,
            self.siren_interval_s,
            self.notification_interval_s,
        )
        last_watchdog = 0.0
        last_flag = 0.0
        last_siren = 0.0
        last_notification = 0.0

        while not self.stop_event.is_set():
            now_mono = time.monotonic()

            # Notification runner executes every 5s, independent of node offline grace period
            if now_mono - last_notification >= self.notification_interval_s:
                try:
                    self.run_notification_once()
                except Exception as exc:
                    logger.exception("Unexpected error in notification cycle execution: %s", exc)
                last_notification = now_mono

            # A2: Siren job runs every min(10, SIREN_REFRESH_S / 3) seconds, independent of WATCHDOG_INTERVAL_S
            siren_interval = min(10.0, float(SIREN_REFRESH_S) / 3.0)
            if now_mono - last_siren >= siren_interval:
                try:
                    self.run_siren_once()
                except Exception as exc:
                    logger.exception("Unexpected error in siren cycle execution: %s", exc)
                last_siren = now_mono

            # Watchdog & flag cycles run according to WATCHDOG_INTERVAL_S
            if not self.in_grace_period():
                if now_mono - last_watchdog >= self.watchdog_interval_s:
                    try:
                        self.run_watchdog_once()
                    except Exception as exc:
                        logger.exception("Unexpected error in watchdog execution: %s", exc)
                    last_watchdog = now_mono

                if now_mono - last_flag >= self.watchdog_interval_s:
                    try:
                        self.run_flag_once()
                    except Exception as exc:
                        logger.exception("Unexpected error in flag cycle execution: %s", exc)
                    last_flag = now_mono

            # Wait in small 0.5s slices or until stop_event is set
            self.stop_event.wait(timeout=0.5)

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
