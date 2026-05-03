"""
run_sync.py
────────────
One-shot Notion sync runner.

Usage:
    python -m services.notion_sync.run_sync
    make sync
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
from services.notion_sync.sync_job import run_sync


def main() -> None:
    print("\n── Notion Sync ──────────────────────────────────────────")
    with get_session() as session:
        synced, failed = run_sync(session)

    if synced == 0 and failed == 0:
        print("✓ No applications to sync.")
    else:
        print(f"✓ Done — synced: {synced} | failed: {failed}")

    if failed > 0:
        print("⚠  Some rows failed — check logs above. They will retry next run.")
    print("─────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n✗ Sync failed: {exc}", file=sys.stderr)
        sys.exit(1)
