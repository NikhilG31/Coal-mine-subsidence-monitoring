"""Pytest configuration and test database isolation fixture suite.

Owns enforcing TEST_DATABASE_URL, isolating the test database, running Alembic migrations,
and truncating tables between test cases.
Must NOT connect to development or production databases.
"""
import os
import sys
import datetime
from pathlib import Path
import pytest
from dotenv import load_dotenv

# Ensure root .env is loaded
_current_file = Path(__file__).resolve()
_repo_root = _current_file.parent.parent.parent
_env_path = _repo_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

# Require TEST_DATABASE_URL and validate database name
TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
if not TEST_DB_URL or not TEST_DB_URL.strip():
    pytest.exit("TEST_DATABASE_URL environment variable is required to run tests.", returncode=1)

TEST_DB_URL = TEST_DB_URL.strip()
if TEST_DB_URL.startswith("postgres://"):
    TEST_DB_URL = TEST_DB_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif TEST_DB_URL.startswith("postgresql://") and not TEST_DB_URL.startswith("postgresql+"):
    TEST_DB_URL = TEST_DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Extract database name to enforce _test suffix
db_name = TEST_DB_URL.rsplit("/", 1)[-1].split("?")[0]
if not db_name.endswith("_test"):
    pytest.exit(
        f"Safety violation: Test database name '{db_name}' must end with '_test'. Aborting.",
        returncode=1,
    )

# Point DATABASE_URL to TEST_DATABASE_URL and disable background watchdog thread BEFORE importing app
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["WATCHDOG_ENABLED"] = "false"

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def apply_migrations():
    """Applies Alembic schema migrations once per test session."""
    server_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = server_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.upgrade(alembic_cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_database():
    """Truncates all tables between test cases for absolute isolation."""
    def _truncate():
        with engine.connect() as conn:
            conn.execute(
                text(
                    "TRUNCATE TABLE check_flags, check_events, qr_tokens, assignments, worker_profiles, alerts, audit_log, readings, nodes, gateways, "
                    "user_mines, users, tunnels, mines, companies RESTART IDENTITY CASCADE;"
                )
            )
            conn.commit()

    _truncate()
    yield
    _truncate()


@pytest.fixture
def db():
    """Provides a transactional database session for a test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """Provides a FastAPI test client bound to the application."""
    return TestClient(app)


@pytest.fixture
def freeze_clock(monkeypatch):
    """Freezes app.services.clock.utcnow to a specified datetime without patching datetime module."""
    def _freeze(frozen_dt: datetime.datetime):
        if frozen_dt.tzinfo is None:
            frozen_dt = frozen_dt.replace(tzinfo=datetime.timezone.utc)
        monkeypatch.setattr("app.services.clock.utcnow", lambda: frozen_dt)
        return frozen_dt

    return _freeze
