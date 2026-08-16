"""
SQLAlchemy engine/session setup for RouteVolt.

SQLite for now (DATABASE_URL below); swapping to Postgres later is just a
matter of changing this URL since everything downstream talks to it through
the engine/session, not raw sqlite3 calls.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///routevolt.db"

# check_same_thread=False is required for SQLite + FastAPI: requests can be
# handled on a different thread than the one that created the engine.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a session, always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
