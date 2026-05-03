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

from db.repository import insert_email_event, gmail_id_exists
from db.session import get_session
from services.classifier.classifier import classify_email
from services.classifier.llm_client import LLMUnavailableError, check_llm_health, LLM_PROVIDER, API_KEY
from services.ingestion.gmail_client import build_service, fetch_new_emails, save_fetch_state
from services.ingestion.token_manager import get_credentials
from email.utils import parsedate_to_datetime


# Rate limiting: pause every LLM_BATCH_SIZE actual LLM calls for LLM_PAUSE_SECONDS.
# Set LLM_BATCH_SIZE=0 to disable pausing (for high-RPM paid APIs like OpenAI/Groq).
_BATCH_SIZE = int(os.environ.get("LLM_BATCH_SIZE",   "20"))
_PAUSE_SEC  = int(os.environ.get("LLM_PAUSE_SECONDS", "60"))


def main() -> None:
    # For external APIs, skip the network health check (slow cold-starts cause
    # false negatives). Just verify the key is present; real errors surface on
    # the first classify call and are caught as LLMUnavailableError below.
    if LLM_PROVIDER == "api":
        if not API_KEY:
            print("✗ API_KEY is not set in .env", file=sys.stderr)
            sys.exit(1)
    elif not check_llm_health():
        print(
            "✗ LLM Provider is not reachable.\n"
            "  Check .env API keys or run: make up",
            file=sys.stderr,
        )
        sys.exit(1)

    creds   = get_credentials()
    service = build_service(creds)
    emails, new_history_id = fetch_new_emails(service)

    if not emails:
        print("✓ No new emails since last run.")
        save_fetch_state(new_history_id)
        return

    print(f"\nFetched {len(emails)} new email(s) — classifying…\n")

    stored = skipped = llm_calls = 0

    with get_session() as session:
        for index, email in enumerate(emails, start=1):

            # Skip immediately if already classified and stored (no LLM call)
            if gmail_id_exists(session, email["gmail_id"]):
                print(f"  ↷ skip  (already stored) {email['subject'][:60]}")
                skipped += 1
                continue

            # ── Rate limit: pause every _BATCH_SIZE LLM calls ────────────────
            # Must happen BEFORE the classify call, tracking actual LLM usage
            # not total loop iterations (non-job emails used to skip this).
            if llm_calls > 0 and llm_calls % _BATCH_SIZE == 0:
                print(f"  [Rate Limit] {llm_calls} LLM calls made. Sleeping {_PAUSE_SEC}s…")
                time.sleep(_PAUSE_SEC)

            try:
                result = classify_email(email)
                llm_calls += 1
            except Exception as exc:
                err = str(exc)
                if "429" in err or "Too Many Requests" in err:
                    print(f"  [Rate Limit 429] Sleeping 60s before retrying…")
                    time.sleep(60)
                    result = classify_email(email)
                    llm_calls += 1
                else:
                    raise

            if not result.is_job_related:
                print(f"  ✗ skip  {email['subject'][:70]}")
                skipped += 1
                continue

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

            marker = "NEW " if inserted else "DUP "
            print(
                f"  ✓ {marker} [{result.category}]  "
                f"{result.company_name}  —  {email['subject'][:60]}"
            )
            if inserted:
                stored += 1

    print(f"\nDone — stored: {stored} | skipped (not job-related / already stored): {skipped}")


    # Save historyId cursor only after all emails are successfully processed
    save_fetch_state(new_history_id)


if __name__ == "__main__":
    try:
        main()
    except LLMUnavailableError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)
