"""
session.py
──────────
SQLAlchemy engine + session factory.

Reads DATABASE_URL from the environment (or .env file via python-dotenv).

Usage:
    from db.session import get_session

    with get_session() as session:
        session.add(some_model)
        session.commit()
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

load_dotenv()  # no-op if already loaded; safe to call multiple times

_DATABASE_URL = os.environ.get("DATABASE_URL")
if not _DATABASE_URL:
    raise EnvironmentError(
        "DATABASE_URL is not set. "
        "Copy .env.example to .env and fill in your connection string."
    )

engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,   # drops stale connections automatically
    pool_size=5,
    max_overflow=10,
    echo=False,           # set to True to log all SQL (useful for debugging)
)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Session:
    """Yield a transactional Session; auto-rollback on exception."""
    session: Session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
