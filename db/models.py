"""
models.py
─────────
SQLAlchemy ORM models matching the EMLI database schema.

Tables
──────
  applications   — one row per unique job application (deduplicated by hash)
  email_events   — one row per classified email from Gmail
"""

import uuid
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base — Alembic autogenerate reads from this."""
    __allow_unmapped__ = True  # allow Column() style alongside Mapped[]


# ─────────────────────────────────────────────────────────────────────────────
# applications
# One row per unique job application. application_id is a deterministic UUID
# derived from SHA-256(company_name + role_title) — see db/hash_utils.py.
# ─────────────────────────────────────────────────────────────────────────────
class Application(Base):
    __tablename__ = "applications"

    application_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        # NOT server-generated — we compute it in Python via make_application_id()
    )
    company_name: str = Column(Text, nullable=False)
    role_title: str | None = Column(Text, nullable=True)
    category: str | None = Column(Text, nullable=True)   # latest resolved category from ETL (see email_events.category)
    applied_date: date | None = Column(Date, nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<Application id={self.application_id} "
            f"company={self.company_name!r} role={self.role_title!r}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# email_events
# One row per Gmail message that was classified as job-application-related.
# gmail_id is the idempotency key — duplicate processing is rejected at DB level.
# ─────────────────────────────────────────────────────────────────────────────
class EmailEvent(Base):
    __tablename__ = "email_events"

    id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    gmail_id: str = Column(Text, unique=True, nullable=False)
    application_id: uuid.UUID | None = Column(
        UUID(as_uuid=True),
        nullable=True,
        # Deterministic hash of (company_name, role_title) — see db/hash_utils.py.
        # No FK: email_events is append-only and does not require an applications row.
    )
    category: str = Column(Text, nullable=False)
    subject: str | None = Column(Text, nullable=True)
    sender: str | None = Column(Text, nullable=True)
    company_name: str = Column(Text, nullable=False)
    role_title: str | None = Column(Text, nullable=True)
    notion_synced: bool = Column(Boolean, default=False, nullable=False)
    received_at: datetime | None = Column(
        DateTime(timezone=True), nullable=True  # actual email date from Gmail headers
    )
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<EmailEvent gmail_id={self.gmail_id!r} "
            f"category={self.category!r} company={self.company_name!r}>"
        )
