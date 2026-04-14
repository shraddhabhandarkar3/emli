"""
run_fetch.py
─────────────
One-shot pipeline run: fetch → classify → store.
Use this for manual testing or ad-hoc runs.

Usage:
    python -m services.ingestion.run_fetch
    make fetch
"""

import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

from db.repository import insert_email_event
from db.session import get_session
from services.classifier.classifier import classify_email
from services.classifier.llm_client import LLMUnavailableError, check_llm_health
from services.ingestion.gmail_client import build_service, fetch_new_emails
from services.ingestion.token_manager import get_credentials


# ── Rate Limiting ────────────────────────────────────────────────────────────
_BATCH_SIZE = 20
_PAUSE_SEC = 60


def main() -> None:
    # Fail fast if Provider isn't up
    if not check_llm_health():
        print(
            "✗ LLM Provider is not reachable.\n"
            "  Check .env API keys or run: make up",
            file=sys.stderr,
        )
        sys.exit(1)

    creds   = get_credentials()
    service = build_service(creds)
    emails  = fetch_new_emails(service)

    if not emails:
        print("✓ No new emails since last run.")
        return

    print(f"\nFetched {len(emails)} new email(s) — classifying…\n")

    stored = skipped = 0

    with get_session() as session:
        # Loop with 1-based index to handle chunking
        for index, email in enumerate(emails, start=1):
            
            result = classify_email(email)

            if not result.is_job_related:
                print(f"  ✗ skip  {email['subject'][:70]}")
                skipped += 1
                continue

            inserted = insert_email_event(
                session,
                gmail_id=email["gmail_id"],
                category=result.category,
                company_name=result.company_name,
                role_title=result.role_title,
                subject=email["subject"],
                sender=email["sender"],
            )

            marker = "NEW " if inserted else "DUP "
            print(
                f"  ✓ {marker} [{result.category}]  "
                f"{result.company_name}  —  {email['subject'][:60]}"
            )
            if inserted:
                stored += 1

            # Pause execution if we have hit the chunk size and there are still more emails to process
            if index % _BATCH_SIZE == 0 and index < len(emails):
                print(f"  [Rate Limit] Processed {index} emails. Sleeping {_PAUSE_SEC}s to respect 40 RPM limit...")
                time.sleep(_PAUSE_SEC)

    print(f"\nDone — stored: {stored} | skipped (not job-related): {skipped}")


if __name__ == "__main__":
    try:
        main()
    except LLMUnavailableError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)
