"""
scheduler.py
─────────────
Orchestrates the full pipeline on a fixed interval:
  1. Fetch new emails from Gmail (incremental via historyId)
  2. Classify each email — relevance check then category + entity extraction
  3. Store job-related emails in Postgres via insert_email_event
  4. Run ETL — rebuild applications table from email_events
  5. Notion Sync — push unsynced applications to Notion database

Configuration (via .env):
  FETCH_INTERVAL_MINUTES   How often to run the cycle (default: 15)

Usage:
    python -m services.ingestion.scheduler
"""

import logging
import os
import sys
import time
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from db.repository import insert_email_event, gmail_id_exists
from db.session import get_session
from services.classifier.classifier import classify_email
from services.classifier.llm_client import LLMUnavailableError, check_llm_health, LLM_PROVIDER, API_KEY
from services.ingestion.gmail_client import build_service, fetch_new_emails, save_fetch_state
from services.ingestion.token_manager import get_credentials
from services.etl.applications_builder import build_applications
from services.notion_sync.sync_job import run_sync

_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "15"))
_INTERVAL_SECONDS = _INTERVAL_MINUTES * 60
# Rate limiting: pause every LLM_BATCH_SIZE calls for LLM_PAUSE_SECONDS.
# Set LLM_BATCH_SIZE=0 to disable pausing (for high-RPM paid APIs like OpenAI).
_BATCH_SIZE  = int(os.environ.get("LLM_BATCH_SIZE",   "20"))
_PAUSE_SEC   = int(os.environ.get("LLM_PAUSE_SECONDS", "60"))



def _run_once() -> tuple[int, int, int]:
    """Run one full pipeline cycle.

    Returns:
        (fetched, job_related, stored) counts.
    """
    # ── 1. Fetch ──────────────────────────────────────────────────────────────
    creds   = get_credentials()
    service = build_service(creds)
    emails, new_history_id = fetch_new_emails(service)

    if not emails:
        logger.info("No new emails.")
        save_fetch_state(new_history_id)
        return 0, 0, 0

    logger.info("Fetched %d email(s) — classifying…", len(emails))

    # ── 2 & 3. Classify + store ───────────────────────────────────────────────
    job_related = 0
    stored      = 0
    llm_calls   = 0

    with get_session() as session:
        for index, email in enumerate(emails, start=1):
            try:
                # Skip immediately if already classified and stored
                if gmail_id_exists(session, email["gmail_id"]):
                    logger.debug("Already stored — skipping %s", email["gmail_id"])
                    continue

                # ── Rate limit: pause every _BATCH_SIZE actual LLM calls ───────
                if _BATCH_SIZE > 0 and llm_calls > 0 and llm_calls % _BATCH_SIZE == 0:
                    logger.info(
                        "Rate limit: %d LLM calls made. Sleeping %ds…",
                        llm_calls, _PAUSE_SEC,
                    )
                    time.sleep(_PAUSE_SEC)

                try:
                    result = classify_email(email)
                    llm_calls += 1
                except LLMUnavailableError as exc:
                    if "429" in str(exc) or "Too Many Requests" in str(exc):
                        logger.warning("Rate limit 429 — sleeping 60s before retry…")
                        time.sleep(60)
                        result = classify_email(email)
                        llm_calls += 1
                    else:
                        raise

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

            # Parse actual email received date from Gmail header
            received_at = None
            raw_date = email.get("date", "")
            if raw_date:
                try:
                    received_at = parsedate_to_datetime(raw_date)
                except Exception:
                    pass

            inserted = insert_email_event(
                session,
                gmail_id=email["gmail_id"],
                category=result.category,
                company_name=result.company_name,
                role_title=result.role_title,
                subject=email["subject"],
                sender=email["sender"],
                received_at=received_at,
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

    # Save historyId cursor only after all emails are successfully processed
    save_fetch_state(new_history_id)

    # ── 4. ETL — rebuild applications table ──────────────────────────────────
    logger.info("Running ETL…")
    with get_session() as session:
        n_apps = build_applications(session)
    logger.info("ETL complete — %d application(s) upserted.", n_apps)

    # ── 5. Notion Sync — push unsynced rows ───────────────────────────────────
    logger.info("Running Notion sync…")
    with get_session() as session:
        synced, failed = run_sync(session)
    logger.info("Notion sync complete — synced: %d | failed: %d", synced, failed)

    return len(emails), job_related, stored


def main() -> None:
    logger.info(
        "Scheduler started — running every %d minute(s). Press Ctrl+C to stop.",
        _INTERVAL_MINUTES,
    )

    # For external APIs, skip the network health check (slow cold-starts cause
    # false negatives). Only check Ollama availability at startup.
    if LLM_PROVIDER != "api" and not check_llm_health():
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
