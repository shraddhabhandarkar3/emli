"""
setup_notion.py
───────────────
Verifies the Notion connection and ensures all required database columns exist.
Run via: make setup-notion
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

token = os.environ.get("NOTION_TOKEN", "")
db_id = os.environ.get("NOTION_DATABASE_ID", "")

if not token or not db_id:
    print("✗  Set NOTION_TOKEN and NOTION_DATABASE_ID in .env first")
    sys.exit(1)

from services.notion_sync.notion_client import ensure_schema, NotionClientError

try:
    ensure_schema(db_id)
    print("✓  Notion schema ready — all columns created/verified")
except NotionClientError as exc:
    print(f"✗  {exc}")
    sys.exit(1)
