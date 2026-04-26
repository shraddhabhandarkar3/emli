"""
applications_builder.py
────────────────────────
ETL script that reads from the append-only `email_events` table, groups events
by application_id, and upserts a current-state summary into `applications`.

Status Priority (highest wins when multiple events exist for one application):
  
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from db.models import EmailEvent
from db.repository import upsert_application

logger = logging.getLogger(__name__)

# Local timezone for date extraction — override with TIMEZONE env var
_LOCAL_TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/Los_Angeles"))
_DATETIME_MIN = datetime.min.replace(tzinfo=timezone.utc)

# ── Status Priority ───────────────────────────────────────────────────────────
# Lower number = higher priority (wins over higher numbers).
_STATUS_PRIORITY: dict[str, int] = {
    "offer_extended":       1,
    "rejected":             2,
    "interview_completed":  3,
    "interview_scheduled":  4,
    "assessment":           5,
    "applied":              6,
    "needs_review":         7,
}

_DEFAULT_PRIORITY = 99  # unknown/future categories — treated as lowest priority


def _priority(category: str) -> int:
    return _STATUS_PRIORITY.get(category, _DEFAULT_PRIORITY)


# ─────────────────────────────────────────────────────────────────────────────
# Core ETL
# ─────────────────────────────────────────────────────────────────────────────

def _event_date(e: "EmailEvent") -> datetime:
    """Return the best available timestamp for an event.

    Prefers received_at (actual email date) over created_at (DB insertion time).
    Falls back to datetime.min so None-safe comparisons always work.
    """
    return e.received_at or e.created_at or _DATETIME_MIN


def _to_local_date(dt: datetime) -> Optional[date]:
    """Convert a UTC/aware datetime to the user's local date."""
    if dt == _DATETIME_MIN:
        return None
    return dt.astimezone(_LOCAL_TZ).date()


def build_applications(session: Session) -> int:
    """Read all email_events, group by application_id, upsert into applications.

    Returns:
        Number of application rows upserted.
    """
    logger.info("ETL: Loading all email_events from database...")
    events: list[EmailEvent] = session.query(EmailEvent).all()

    if not events:
        logger.info("ETL: No email events found — nothing to process.")
        return 0

    logger.info("ETL: Processing %d events...", len(events))

    # Group events by application_id (UUID used as grouping key)
    groups: dict = defaultdict(list)
    for event in events:
        groups[event.application_id].append(event)

    upserted = 0

    for app_id, group_events in groups.items():
        # Sort by event date descending so index 0 = most recent
        group_events.sort(key=_event_date, reverse=True)

        # ── Best status: pick the highest-priority category ───────────────────
        best_status = min(group_events, key=lambda e: _priority(e.category))
        current_category = best_status.category

        # ── Company / Role: from the oldest event that has a role (most authoritative) ─
        events_with_role = [e for e in group_events if e.role_title]
        if events_with_role:
            # Prefer oldest applied event with a role; fall back to any oldest with role
            applied_with_role = [e for e in events_with_role if e.category == "applied"]
            reference = min(applied_with_role or events_with_role, key=_event_date)
        else:
            # All events have null role — use the oldest event for company at least
            reference = min(group_events, key=_event_date)
        company_name = reference.company_name
        role_title = reference.role_title

        # ── Applied date: earliest 'applied' event, fall back to oldest event ─
        applied_events = [e for e in group_events if e.category == "applied"]
        if applied_events:
            earliest_applied = min(applied_events, key=_event_date)
            applied_date: Optional[date] = _to_local_date(_event_date(earliest_applied))
        else:
            oldest = min(group_events, key=_event_date)
            applied_date = _to_local_date(_event_date(oldest))

        upsert_application(
            session,
            application_id=app_id,
            company_name=company_name,
            role_title=role_title,
            status=current_category,
            applied_date=applied_date,
        )
        upserted += 1

        logger.debug(
            "  Upserted: [%s] %s — %s (applied: %s)",
            current_category, company_name, role_title or "N/A", applied_date,
        )

    logger.info("ETL: Upserted %d application(s).", upserted)
    return upserted

