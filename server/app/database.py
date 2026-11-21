"""Database connectivity and session factory.

Owns the SQLAlchemy engine, session maker, Base declarative class, and get_db dependency.
Must NOT define specific table schemas, model relationships, or API route logic.
"""
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Provides a database session scope for FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
