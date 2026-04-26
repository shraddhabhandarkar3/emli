"""
run_etl.py
──────────
One-shot ETL runner: reads email_events → upserts applications table.

Usage:
    python -m services.etl.run_etl
    make etl
"""

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

from db.session import get_session
from services.etl.applications_builder import build_applications


def main() -> None:
    print("\n── Applications ETL ─────────────────────────────────────")
    with get_session() as session:
        upserted = build_applications(session)

    if upserted == 0:
        print("✓ No events to process — applications table is already up to date.")
    else:
        print(f"✓ Done — {upserted} application(s) upserted into 'applications' table.")
    print("─────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n✗ ETL failed: {exc}", file=sys.stderr)
        sys.exit(1)
