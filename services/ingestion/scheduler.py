"""
scheduler.py
─────────────
Cron wrapper for the Gmail ingestion pipeline.

Runs fetch_new_emails() on a fixed interval, indefinitely.
Designed to be the main process of the ingestion Docker container.

Configuration (via .env):
  FETCH_INTERVAL_MINUTES   How often to fetch (default: 15)

Usage:
    python -m services.ingestion.scheduler
"""

import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from services.ingestion.token_manager import get_credentials
from services.ingestion.gmail_client import build_service, fetch_new_emails

_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "15"))
_INTERVAL_SECONDS = _INTERVAL_MINUTES * 60


def _run_once() -> int:
    """Run a single fetch cycle. Returns number of new emails found."""
    creds = get_credentials()
    service = build_service(creds)
    emails = fetch_new_emails(service)

    if emails:
        logger.info("Fetched %d new email(s):", len(emails))
        for email in emails:
            logger.info("  [%s] %s — %s", email["date"], email["sender"], email["subject"])
    else:
        logger.info("No new emails.")

    return len(emails)


def main() -> None:
    logger.info(
        "Scheduler started — running every %d minute(s). Press Ctrl+C to stop.",
        _INTERVAL_MINUTES,
    )

    while True:
        try:
            _run_once()
        except Exception:
            logger.exception("Fetch cycle failed — will retry next interval.")

        logger.info("Sleeping %d minute(s)…", _INTERVAL_MINUTES)
        time.sleep(_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        sys.exit(0)
