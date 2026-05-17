"""
setup_notion.py
───────────────
Creates the Applications database inside your Notion page.
Run via: make setup-notion

Required in .env:
  NOTION_TOKEN    — your Notion integration secret
  NOTION_PAGE_ID  — the Notion page to create the database in

After running, add the printed NOTION_DATABASE_ID to your .env.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN      = os.environ.get("NOTION_TOKEN", "")
NOTION_PAGE_ID    = os.environ.get("NOTION_PAGE_ID", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

if not NOTION_TOKEN:
    print("✗  Set NOTION_TOKEN in .env first")
    sys.exit(1)
if not NOTION_PAGE_ID:
    print("✗  Set NOTION_PAGE_ID in .env first")
    sys.exit(1)

from notion_client.errors import APIResponseError
from services.notion_sync.notion_client import NotionClientError, create_database

if NOTION_DATABASE_ID:
    print("✓  Notion already configured.")
    print(f"   NOTION_DATABASE_ID={NOTION_DATABASE_ID}")
    sys.exit(0)

try:
    db_id = create_database(NOTION_PAGE_ID)
except (NotionClientError, APIResponseError) as exc:
    print(f"✗  {exc}")
    sys.exit(1)

from dotenv import set_key
set_key(".env", "NOTION_DATABASE_ID", db_id)
print("✓  Applications database created.")
print(f"   NOTION_DATABASE_ID={db_id} written to .env")
