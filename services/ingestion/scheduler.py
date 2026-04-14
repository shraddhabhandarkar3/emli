"""
scheduler.py
─────────────
Orchestrates the full ingestion pipeline on a fixed interval:
  1. Fetch new emails from Gmail (incremental via historyId)
  2. Classify each email — relevance check then category + entity extraction
  3. Store job-related emails in Postgres via insert_email_event

Configuration (via .env):
  FETCH_INTERVAL_MINUTES   How often to run the cycle (default: 15)

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

from db.repository import insert_email_event
from db.session import get_session
from services.classifier.classifier import classify_email
from services.classifier.llm_client import LLMUnavailableError, check_llm_health
from services.ingestion.gmail_client import build_service, fetch_new_emails
from services.ingestion.token_manager import get_credentials

_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "15"))
_INTERVAL_SECONDS = _INTERVAL_MINUTES * 60

# ── Rate Limiting ────────────────────────────────────────────────────────────
_BATCH_SIZE = 20
_PAUSE_SEC = 60


def _run_once() -> tuple[int, int, int]:
    """Run one full pipeline cycle.

    Returns:
        (fetched, job_related, stored) counts.
    """
    # ── 1. Fetch ──────────────────────────────────────────────────────────────
    creds   = get_credentials()
    service = build_service(creds)
    emails  = fetch_new_emails(service)

    if not emails:
        logger.info("No new emails.")
        return 0, 0, 0

    logger.info("Fetched %d email(s) — classifying…", len(emails))

    # ── 2 & 3. Classify + store ───────────────────────────────────────────────
    job_related = 0
    stored      = 0

    with get_session() as session:
        for index, email in enumerate(emails, start=1):
            try:
                result = classify_email(email)
            except LLMUnavailableError:
                logger.error(
                    "LLM Provider is unavailable — aborting this cycle. "
                    "Emails will be re-fetched next run via historyId."
                )
                raise  # bubble up so the scheduler logs it and waits

            status = "✓ job" if result.is_job_related else "✗ skip"
            logger.info(
                "  %s  %-8s  [%s]  %s",
                status,
                result.category or "",
                result.company_name,
                email["subject"][:70],
            )

            if not result.is_job_related:
                continue

            job_related += 1
            inserted = insert_email_event(
                session,
                gmail_id=email["gmail_id"],
                category=result.category,
                company_name=result.company_name,
                role_title=result.role_title,
                subject=email["subject"],
                sender=email["sender"],
            )
            if inserted:
                stored += 1
                
            if index % _BATCH_SIZE == 0 and index < len(emails):
                logger.info("Rate Limit: Processed %d emails. Sleeping %ds to respect API limits...", index, _PAUSE_SEC)
                time.sleep(_PAUSE_SEC)

    logger.info(
        "Cycle complete — fetched: %d | job-related: %d | stored: %d",
        len(emails), job_related, stored,
    )
    return len(emails), job_related, stored


def main() -> None:
    logger.info(
        "Scheduler started — running every %d minute(s). Press Ctrl+C to stop.",
        _INTERVAL_MINUTES,
    )

    # Warn early if Provider isn't reachable
    if not check_llm_health():
        logger.warning(
            "LLM Provider is not reachable at startup. "
            "Check .env API key or run `make up` for Ollama."
        )

    while True:
        try:
            _run_once()
        except LLMUnavailableError:
            logger.error("LLM Provider unreachable — will retry next interval.")
        except Exception:
            logger.exception("Unexpected error in pipeline cycle.")

        logger.info("Sleeping %d minute(s)…", _INTERVAL_MINUTES)
        time.sleep(_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        sys.exit(0)
