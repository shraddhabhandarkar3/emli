"""
backfill_received_at.py
────────────────────────
One-time script to populate email_events.received_at for all existing rows
that have received_at = NULL.

Uses the Gmail API's metadata-only fetch (very lightweight — no body download)
to retrieve just the Date header for each email.

Usage:
    python backfill_received_at.py
"""

import logging
import os
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from db.models import EmailEvent
from db.session import get_session
from services.ingestion.gmail_client import build_service
from services.ingestion.token_manager import get_credentials


def backfill(dry_run: bool = False) -> None:
    creds = get_credentials()
    service = build_service(creds)

    with get_session() as session:
        # Find all rows missing received_at
        rows = (
            session.query(EmailEvent)
            .filter(EmailEvent.received_at.is_(None))
            .all()
        )
        logger.info("Found %d email_events with received_at = NULL", len(rows))

        if not rows:
            logger.info("Nothing to backfill — all rows already have received_at.")
            return

        updated = 0
        failed = 0

        for i, event in enumerate(rows, 1):
            if i % 50 == 0 or i == len(rows):
                logger.info("Progress: %d / %d", i, len(rows))

            try:
                # Lightweight fetch — metadata only, just the Date header
                msg = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=event.gmail_id,
                        format="metadata",
                        metadataHeaders=["Date"],
                    )
                    .execute()
                )

                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                raw_date = headers.get("date", "")

                if not raw_date:
                    logger.warning("No Date header for %s — skipping", event.gmail_id)
                    failed += 1
                    continue

                received_at = parsedate_to_datetime(raw_date)

                if not dry_run:
                    event.received_at = received_at

                updated += 1
                logger.debug("  ✓ %s → %s", event.gmail_id, received_at.date())

            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", event.gmail_id, exc)
                failed += 1

        if not dry_run:
            logger.info("Committing %d updated rows…", updated)
            session.commit()

        logger.info(
            "Backfill complete — updated: %d | failed/skipped: %d%s",
            updated,
            failed,
            " (DRY RUN — no changes written)" if dry_run else "",
        )


if __name__ == "__main__":
    backfill(dry_run=False)
