"""Centralized configuration manager.

Owns loading environment variables from .env and providing typed application settings.
Must NOT initiate database connections, perform I/O beyond .env loading, or import FastAPI.
"""
import os
from pathlib import Path
from typing import List, Optional
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
OFFLINE_AFTER_S: int = int(os.getenv("OFFLINE_AFTER_S", "35"))

# Feature Flags
ENABLE_DEMO_ROUTES: bool = os.getenv("ENABLE_DEMO_ROUTES", "false").lower() in ("true", "1", "yes")

# Seed Manager Credentials (CLI / Environment fallback)
SEED_COMPANY_NAME: Optional[str] = os.getenv("SEED_COMPANY_NAME")
SEED_MANAGER_USERNAME: Optional[str] = os.getenv("SEED_MANAGER_USERNAME")
SEED_MANAGER_PASSWORD: Optional[str] = os.getenv("SEED_MANAGER_PASSWORD")
SEED_MANAGER_FULL_NAME: str = os.getenv("SEED_MANAGER_FULL_NAME", "General Manager")

# Phase B: Security and Session Authentication Configuration
JWT_SECRET: str = os.getenv("JWT_SECRET", "")
SESSION_HOURS: int = int(os.getenv("SESSION_HOURS", "8"))
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")

_cors_origins_raw = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: List[str] = [
    origin.strip() for origin in _cors_origins_raw.split(",") if origin.strip()
]


def validate_jwt_secret() -> None:
    """Enforces that JWT_SECRET is configured and has at least 32 characters."""
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        raise ValueError(
            "JWT_SECRET environment variable is required and must be at least 32 characters long."
        )
