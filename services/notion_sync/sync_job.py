"""
sync_job.py
────────────
Main Notion sync orchestration.

Reads applications + email stats from Postgres and upserts each
row into the configured Notion database. On success, marks the
related email_events rows as notion_synced = TRUE.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from db.repository import get_applications_with_stats, mark_notion_synced
from services.notion_sync.notion_client import (
    NotionClientError,
    ensure_schema,
    find_page,
    upsert_page,
)

load_dotenv()

logger = logging.getLogger(__name__)

NOTION_DATABASE_ID: str = os.environ.get("NOTION_DATABASE_ID", "")


def run_sync(session: Session) -> tuple[int, int]:
    """Sync all applications to Notion.

    Returns:
        (synced_count, failed_count)
    """
    if not NOTION_DATABASE_ID:
        raise ValueError("NOTION_DATABASE_ID is missing from environment.")

    # ── 1. Ensure database schema is correct ─────────────────────────────────
    logger.info("Sync: Verifying Notion database schema...")
    ensure_schema(NOTION_DATABASE_ID)

    # ── 2. Pull applications + stats from Postgres ───────────────────────────
    apps = get_applications_with_stats(session)
    logger.info("Sync: Processing %d application(s)...", len(apps))

    synced = 0
    failed = 0

    for app in apps:
        company = app["company_name"]
        role    = app.get("role_title")

        try:
            # ── 3. Find or create Notion page ─────────────────────────────────
            page_id = find_page(NOTION_DATABASE_ID, company, role)
            action  = "Updated" if page_id else "Created"

            upsert_page(NOTION_DATABASE_ID, app, page_id=page_id)

            # ── 4. Mark email events as synced ────────────────────────────────
            if app.get("event_ids"):
                mark_notion_synced(session, app["event_ids"])

            synced += 1
            logger.info(
                "  ✓ %s  [%s]  %s — %s",
                action,
                app.get("category") or "?",
                company,
                role or "N/A",
            )

        except Exception as exc:
            failed += 1
            logger.error(
                "  ✗ Failed to sync %s / %s: %s", company, role, exc
            )
            # Continue to next application — DB is source of truth

    logger.info(
        "Sync complete — synced: %d | failed: %d", synced, failed
    )
    return synced, failed
