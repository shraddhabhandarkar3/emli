"""
applications_builder.py
────────────────────────
ETL script that reads from the append-only `email_events` table, groups events
by application_id, and upserts a current-state summary into `applications`.

Status Priority (highest wins when multiple events exist for one application):
  offer_extended > rejected > interview_completed > interview_scheduled
  > assessment > applied > needs_review
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from db.models import EmailEvent
from db.repository import upsert_application

logger = logging.getLogger(__name__)

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
        # Sort by created_at descending so index 0 = most recent
        group_events.sort(
            key=lambda e: e.created_at if e.created_at else date.min,
            reverse=True,
        )

        # ── Best status: pick the highest-priority category ───────────────────
        best_status = min(group_events, key=lambda e: _priority(e.category))
        current_category = best_status.category

        # ── Company / Role: from the most recent event ────────────────────────
        most_recent = group_events[0]
        company_name = most_recent.company_name
        role_title = most_recent.role_title

        # ── Applied date: earliest 'applied' event, fall back to oldest event ─
        applied_events = [e for e in group_events if e.category == "applied"]
        if applied_events:
            # Oldest applied event
            earliest_applied = min(
                applied_events,
                key=lambda e: e.created_at if e.created_at else date.max,
            )
            applied_date: Optional[date] = (
                earliest_applied.created_at.date()
                if earliest_applied.created_at
                else None
            )
        else:
            # No 'applied' event — fall back to oldest event's date
            oldest = min(
                group_events,
                key=lambda e: e.created_at if e.created_at else date.max,
            )
            applied_date = oldest.created_at.date() if oldest.created_at else None

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
