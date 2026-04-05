"""
repository.py
─────────────
CRUD layer for the EMLI database. All application code should go through
these functions — no raw SQL elsewhere.

Design principles:
  • Each function accepts a session parameter so callers control transactions.
  • Upsert semantics on applications (insert-or-ignore on PK collision).
  • Idempotent email_events insertion (UNIQUE gmail_id → skip if duplicate).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.hash_utils import make_application_id
from db.models import Application, EmailEvent


# ─────────────────────────────────────────────────────────────────────────────
# Applications
# ─────────────────────────────────────────────────────────────────────────────

def upsert_application(
    session: Session,
    company_name: str,
    role_title: Optional[str],
    applied_date: Optional[date] = None,
) -> uuid.UUID:
    """Insert a new application row, or do nothing if the hash already exists.

    Returns the application_id UUID so callers can use it immediately.
    """
    app_id = make_application_id(company_name, role_title)

    stmt = (
        pg_insert(Application)
        .values(
            application_id=app_id,
            company_name=company_name,
            role_title=role_title,
            applied_date=applied_date,
        )
        .on_conflict_do_update(
            index_elements=["application_id"],
            # Only update applied_date if we now have a value and didn't before
            set_={
                "updated_at": datetime.utcnow(),
                "applied_date": pg_insert(Application)
                .excluded.applied_date,
            },
            where=Application.applied_date.is_(None),
        )
    )
    session.execute(stmt)
    return app_id


def get_application(session: Session, application_id: uuid.UUID) -> Optional[Application]:
    """Fetch an application by its hash UUID."""
    return session.get(Application, application_id)


# ─────────────────────────────────────────────────────────────────────────────
# Email Events
# ─────────────────────────────────────────────────────────────────────────────

def is_email_processed(session: Session, gmail_id: str) -> bool:
    """Return True if this Gmail message has already been stored."""
    stmt = select(EmailEvent.gmail_id).where(EmailEvent.gmail_id == gmail_id).limit(1)
    return session.execute(stmt).scalar() is not None


def insert_email_event(
    session: Session,
    *,
    gmail_id: str,
    category: str,
    company_name: str,
    role_title: Optional[str] = None,
    subject: Optional[str] = None,
    sender: Optional[str] = None,
    applied_date: Optional[date] = None,
) -> Optional[EmailEvent]:
    """Insert a classified email event.

    Steps:
      1. Check idempotency — skip if gmail_id already in DB.
      2. Upsert the parent application row (creates if new, no-op if exists).
      3. Insert the email_event row linked to that application.

    Returns the new EmailEvent, or None if already processed.
    """
    if is_email_processed(session, gmail_id):
        return None

    app_id = upsert_application(session, company_name, role_title, applied_date)

    event = EmailEvent(
        gmail_id=gmail_id,
        application_id=app_id,
        category=category,
        subject=subject,
        sender=sender,
        company_name=company_name,
        role_title=role_title,
    )
    session.add(event)
    return event


def get_unsynced_events(session: Session) -> list[EmailEvent]:
    """Return all email events not yet pushed to Notion."""
    stmt = select(EmailEvent).where(EmailEvent.notion_synced.is_(False))
    return list(session.execute(stmt).scalars().all())


def mark_notion_synced(session: Session, event_ids: list[uuid.UUID]) -> int:
    """Mark a batch of email events as synced to Notion.

    Returns the number of rows updated.
    """
    if not event_ids:
        return 0
    stmt = (
        update(EmailEvent)
        .where(EmailEvent.id.in_(event_ids))
        .values(notion_synced=True)
    )
    result = session.execute(stmt)
    return result.rowcount
