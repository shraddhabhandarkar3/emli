"""
run_fetch.py
─────────────
Entry-point script for the Gmail ingestion step.

Fetches new emails incrementally from the inbox and prints a preview.
T2 (classifier) will plug in here to classify + write to DB.

Usage:
    python -m services.ingestion.run_fetch
"""

import logging
import sys

from dotenv import load_dotenv

# Load .env before any other project imports
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

from services.ingestion.token_manager import get_credentials
from services.ingestion.gmail_client import build_service, fetch_new_emails


def main() -> None:
    creds = get_credentials()
    service = build_service(creds)
    emails = fetch_new_emails(service)

    if not emails:
        print("✓ No new emails since last run.")
        return

    print(f"\n✓ Fetched {len(emails)} new email(s):\n")
    for email in emails:
        preview = email["body_text"][:120].replace("\n", " ").strip()
        print(f"  [{email['date']}]")
        print(f"  From    : {email['sender']}")
        print(f"  Subject : {email['subject']}")
        print(f"  Preview : {preview}…")
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
