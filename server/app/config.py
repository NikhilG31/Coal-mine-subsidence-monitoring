"""Centralized configuration manager.

Owns loading environment variables from .env and providing typed application settings.
Must NOT initiate database connections, perform I/O beyond .env loading, or import FastAPI.
"""
import os
from pathlib import Path
from typing import List, Optional
import zoneinfo
from dotenv import load_dotenv

# Search for .env at repo root, then server root
_current_file = Path(__file__).resolve()
_repo_root = _current_file.parent.parent.parent
_env_path = _repo_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

# Database Configuration (PostgreSQL with psycopg v3 driver only)
_raw_database_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
if not _raw_database_url or not _raw_database_url.strip():
    raise ValueError("DATABASE_URL environment variable is required and has no default.")

_db_url_clean = _raw_database_url.strip()
if _db_url_clean.startswith("postgres://"):
    _db_url_clean = _db_url_clean.replace("postgres://", "postgresql+psycopg://", 1)
elif _db_url_clean.startswith("postgresql://") and not _db_url_clean.startswith("postgresql+"):
    _db_url_clean = _db_url_clean.replace("postgresql://", "postgresql+psycopg://", 1)

DATABASE_URL: str = _db_url_clean

# Telemetry and Silence Monitoring Thresholds
_offline_raw = os.getenv("NODE_OFFLINE_AFTER_S") or os.getenv("OFFLINE_AFTER_S", "35")
NODE_OFFLINE_AFTER_S: int = int(_offline_raw)
OFFLINE_AFTER_S: int = NODE_OFFLINE_AFTER_S  # Alias for backward compatibility

GATEWAY_OFFLINE_AFTER_S: int = int(os.getenv("GATEWAY_OFFLINE_AFTER_S", "60"))
WATCHDOG_INTERVAL_S: int = int(os.getenv("WATCHDOG_INTERVAL_S", "30"))
WATCHDOG_ENABLED: bool = os.getenv("WATCHDOG_ENABLED", "true").lower() in ("true", "1", "yes")

if NODE_OFFLINE_AFTER_S <= 0:
    raise ValueError("NODE_OFFLINE_AFTER_S must be positive")
if GATEWAY_OFFLINE_AFTER_S <= 0:
    raise ValueError("GATEWAY_OFFLINE_AFTER_S must be positive")
if WATCHDOG_INTERVAL_S <= 0:
    raise ValueError("WATCHDOG_INTERVAL_S must be positive")
if GATEWAY_OFFLINE_AFTER_S < NODE_OFFLINE_AFTER_S:
    raise ValueError("GATEWAY_OFFLINE_AFTER_S must be greater than or equal to NODE_OFFLINE_AFTER_S")

# Feature Flags
ENABLE_DEMO_ROUTES: bool = os.getenv("ENABLE_DEMO_ROUTES", "false").lower() in ("true", "1", "yes")

# Seed Manager Credentials (CLI / Environment fallback)
SEED_COMPANY_NAME: Optional[str] = os.getenv("SEED_COMPANY_NAME")
SEED_MANAGER_USERNAME: Optional[str] = os.getenv("SEED_MANAGER_USERNAME")
SEED_MANAGER_PASSWORD: Optional[str] = os.getenv("SEED_MANAGER_PASSWORD")
SEED_MANAGER_FULL_NAME: str = os.getenv("SEED_MANAGER_FULL_NAME", "General Manager")

# Phase B: Gateway Key and Compatibility Configuration
ALLOW_UNKEYED_INGEST: bool = os.getenv("ALLOW_UNKEYED_INGEST", "false").lower() in ("true", "1", "yes")
DEMO_GATEWAY_KEY: str = os.getenv("DEMO_GATEWAY_KEY", "")


def validate_demo_gateway_key() -> str:
    """Validates and returns DEMO_GATEWAY_KEY, requiring >= 32 chars, no CHANGE_ME, >= 12 distinct chars."""
    if not DEMO_GATEWAY_KEY or len(DEMO_GATEWAY_KEY) < 32:
        raise ValueError(
            "DEMO_GATEWAY_KEY environment variable is required and must be at least 32 characters long."
        )
    if "change_me" in DEMO_GATEWAY_KEY.lower():
        raise ValueError("DEMO_GATEWAY_KEY cannot contain 'CHANGE_ME'.")
    if len(set(DEMO_GATEWAY_KEY)) < 12:
        raise ValueError("DEMO_GATEWAY_KEY must contain at least 12 distinct characters.")
    return DEMO_GATEWAY_KEY
JWT_SECRET: str = os.getenv("JWT_SECRET", "")
SESSION_HOURS: int = int(os.getenv("SESSION_HOURS", "8"))
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")

_cors_origins_raw = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: List[str] = [
    origin.strip() for origin in _cors_origins_raw.split(",") if origin.strip()
]


# Step 5: Shift and Work Hours Configuration
WEEKLY_HOURS_LIMIT: float = float(os.getenv("WEEKLY_HOURS_LIMIT", "48"))
MAX_SHIFT_HOURS: float = float(os.getenv("MAX_SHIFT_HOURS", "16"))
HOURS_TIMEZONE: str = os.getenv("HOURS_TIMEZONE", "Asia/Kolkata").strip()

if WEEKLY_HOURS_LIMIT <= 0:
    raise ValueError("WEEKLY_HOURS_LIMIT must be positive")
if MAX_SHIFT_HOURS <= 0:
    raise ValueError("MAX_SHIFT_HOURS must be positive")

try:
    zoneinfo.ZoneInfo(HOURS_TIMEZONE)
except Exception as e:
    raise ValueError(f"HOURS_TIMEZONE '{HOURS_TIMEZONE}' is not a valid IANA timezone: {e}")


# Step 6: Badge, Check-in, Headcount, and Check Flags Configuration
CHECKOUT_GRACE_MIN: int = int(os.getenv("CHECKOUT_GRACE_MIN", "15"))
CHECKIN_EARLY_MIN: int = int(os.getenv("CHECKIN_EARLY_MIN", "120"))
CHECK_FLAG_OVERDUE_HOURS: int = int(os.getenv("CHECK_FLAG_OVERDUE_HOURS", "8"))
SCAN_RATE_PER_MIN: int = int(os.getenv("SCAN_RATE_PER_MIN", "60"))

if CHECKOUT_GRACE_MIN < 0:
    raise ValueError("CHECKOUT_GRACE_MIN must be non-negative")
if CHECKIN_EARLY_MIN < 0:
    raise ValueError("CHECKIN_EARLY_MIN must be non-negative")
if CHECK_FLAG_OVERDUE_HOURS <= 0:
    raise ValueError("CHECK_FLAG_OVERDUE_HOURS must be positive")
if SCAN_RATE_PER_MIN <= 0:
    raise ValueError("SCAN_RATE_PER_MIN must be positive")


# Step 7: Incidents, Roll Call, Node Commands, and Siren Configuration
COMMAND_TTL_S: int = int(os.getenv("COMMAND_TTL_S", "120"))
SIREN_REFRESH_S: int = int(os.getenv("SIREN_REFRESH_S", "30"))
COMMANDS_REQUIRE_HTTPS: bool = os.getenv("COMMANDS_REQUIRE_HTTPS", "true").lower() in ("true", "1", "yes")
TRUST_FORWARDED_PROTO: bool = os.getenv("TRUST_FORWARDED_PROTO", "false").lower() in ("true", "1", "yes")
COMMAND_RETENTION_DAYS: int = int(os.getenv("COMMAND_RETENTION_DAYS", "30"))
AUTO_INCIDENT_SIREN: bool = os.getenv("AUTO_INCIDENT_SIREN", "true").lower() in ("true", "1", "yes")

if COMMAND_TTL_S <= 0:
    raise ValueError("COMMAND_TTL_S must be positive")
if not (5 <= SIREN_REFRESH_S <= 40):
    raise ValueError("SIREN_REFRESH_S must be between 5 and 40 seconds")
if COMMAND_RETENTION_DAYS <= 0:
    raise ValueError("COMMAND_RETENTION_DAYS must be positive")


# Step 8: Worker Reports and Device SOS Configuration
SOS_MERGE_WINDOW_S: int = int(os.getenv("SOS_MERGE_WINDOW_S", "60"))
SOS_MAX_NEW_PER_HOUR: int = int(os.getenv("SOS_MAX_NEW_PER_HOUR", "6"))
SOS_INCIDENT_SIREN: bool = os.getenv("SOS_INCIDENT_SIREN", "false").lower() in ("true", "1", "yes")
REPORT_RATE_PER_HOUR: int = int(os.getenv("REPORT_RATE_PER_HOUR", "20"))
REPORT_MAX_AGE_DAYS_WORKER_VIEW: int = int(os.getenv("REPORT_MAX_AGE_DAYS_WORKER_VIEW", "90"))

if SOS_MERGE_WINDOW_S <= 0:
    raise ValueError("SOS_MERGE_WINDOW_S must be positive")
if SOS_MAX_NEW_PER_HOUR <= 0:
    raise ValueError("SOS_MAX_NEW_PER_HOUR must be positive")
if REPORT_RATE_PER_HOUR <= 0:
    raise ValueError("REPORT_RATE_PER_HOUR must be positive")
if REPORT_MAX_AGE_DAYS_WORKER_VIEW <= 0:
    raise ValueError("REPORT_MAX_AGE_DAYS_WORKER_VIEW must be positive")


# Step 9: Mine Profile and Output Tracking Configuration
SHIFT_LOOKAHEAD_H: int = int(os.getenv("SHIFT_LOOKAHEAD_H", "12"))
if SHIFT_LOOKAHEAD_H <= 0:
    raise ValueError("SHIFT_LOOKAHEAD_H must be positive")


def validate_jwt_secret() -> None:
    """Enforces that JWT_SECRET is configured, >= 32 chars, >= 12 distinct chars, and no CHANGE_ME."""
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        raise ValueError(
            "JWT_SECRET environment variable is required and must be at least 32 characters long."
        )
    if "change_me" in JWT_SECRET.lower():
        raise ValueError("JWT_SECRET cannot contain 'CHANGE_ME'.")
    if len(set(JWT_SECRET)) < 12:
        raise ValueError("JWT_SECRET must contain at least 12 distinct characters.")
