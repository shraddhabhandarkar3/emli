"""
repository.py
─────────────
CRUD layer for the EMLI database. All application code should go through
these functions — no raw SQL elsewhere.

Design principles:
  • Each function accepts a session parameter so callers control transactions.
  • email_events is append-only — duplicate gmail_ids are caught by the DB UNIQUE constraint.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.hash_utils import make_application_id
from db.models import EmailEvent


# ─────────────────────────────────────────────────────────────────────────────
# Email Events
# ─────────────────────────────────────────────────────────────────────────────

def insert_email_event(
    session: Session,
    *,
    gmail_id: str,
    category: str,
    company_name: str,
    role_title: Optional[str] = None,
    subject: Optional[str] = None,
    sender: Optional[str] = None,
) -> bool:
    """Append-only logger for classified email events.

    Uses INSERT ... ON CONFLICT (gmail_id) DO NOTHING so duplicates are
    silently skipped at the DB level — no pre-flight SELECT, no exception.

    Does NOT touch the applications table. The application_id stored here
    is the SHA-256 hash of (company_name, role_title), used purely as a
    grouping key. The separate applications script owns that table.

    Returns:
        True  — row was inserted (new email).
        False — row was skipped (gmail_id already exists).
    """
    stmt = (
        pg_insert(EmailEvent)
        .values(
            gmail_id=gmail_id,
            application_id=make_application_id(company_name, role_title),
            category=category,
            subject=subject,
            sender=sender,
            company_name=company_name,
            role_title=role_title,
        )
        .on_conflict_do_nothing(index_elements=["gmail_id"])
    )
    result = session.execute(stmt)
    return result.rowcount == 1


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
