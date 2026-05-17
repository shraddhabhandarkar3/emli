"""
notion_client.py
─────────────────
Thin wrapper around the Notion SDK.

Responsibilities:
  - create_database : create the Applications database inside a Notion page
  - find_page       : query Notion for a matching row by Company + Role
  - upsert_page     : create or patch a Notion page from an application payload
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

logger = logging.getLogger(__name__)

NOTION_TOKEN: str = os.environ.get("NOTION_TOKEN", "")
_NOTION_VERSION = "2026-03-11"

_DB_PROPERTIES: dict[str, Any] = {
    "Company":       {"title": {}},
    "Role":          {"rich_text": {}},
    "Status":        {"select": {}},
    "Applied Date":  {"date": {}},
    "Last Activity": {"date": {}},
    "Email Count":   {"number": {"format": "number"}},
    "Needs Review":  {"checkbox": {}},
}


class NotionClientError(Exception):
    """Raised for configuration errors (missing token or IDs)."""


def _client() -> Client:
    if not NOTION_TOKEN:
        raise NotionClientError("NOTION_TOKEN is missing from environment.")
    return Client(auth=NOTION_TOKEN, notion_version=_NOTION_VERSION)


# ─────────────────────────────────────────────────────────────────────────────
# Database setup
# ─────────────────────────────────────────────────────────────────────────────

def create_database(page_id: str) -> str:
    """Create the Applications database inside a Notion page.

    Returns the new database ID (no dashes).
    """
    result = _client().databases.create(
        parent={"type": "page_id", "page_id": page_id},
        title=[{"type": "text", "text": {"content": "Applications"}}],
        initial_data_source={"properties": _DB_PROPERTIES},
    )
    return result["id"].replace("-", "")


# ─────────────────────────────────────────────────────────────────────────────
# Page lookup
# ─────────────────────────────────────────────────────────────────────────────

def find_page(database_id: str, company_name: str, role_title: Optional[str] = None) -> Optional[str]:
    """Return the page_id of a matching row in the database, or None."""
    filters: list[dict] = [
        {"property": "Company", "title": {"equals": company_name}},
    ]
    if role_title:
        filters.append({"property": "Role", "rich_text": {"equals": role_title}})

    resp = _client().databases.query(
        database_id=database_id,
        filter={"and": filters} if len(filters) > 1 else filters[0],
    )
    results = resp.get("results", [])
    return results[0]["id"] if results else None


# ─────────────────────────────────────────────────────────────────────────────
# Page upsert
# ─────────────────────────────────────────────────────────────────────────────

def _build_properties(payload: dict) -> dict:
    props: dict[str, Any] = {
        "Company":     {"title": [{"text": {"content": payload["company_name"] or ""}}]},
        "Role":        {"rich_text": [{"text": {"content": payload["role_title"] or ""}}]},
        "Status":      {"select": {"name": payload["category"] or "needs_review"}},
        "Email Count": {"number": payload.get("email_count", 0)},
        "Needs Review": {"checkbox": bool(payload.get("needs_review", False))},
    }
    if applied := payload.get("applied_date"):
        props["Applied Date"] = {"date": {"start": str(applied)}}
    if last_activity := payload.get("last_activity"):
        if hasattr(last_activity, "date"):
            last_activity = last_activity.date()
        props["Last Activity"] = {"date": {"start": str(last_activity)}}
    return props


def upsert_page(database_id: str, payload: dict, page_id: Optional[str] = None) -> str:
    """Create or update a Notion page. Returns the Notion page_id."""
    client = _client()
    properties = _build_properties(payload)
    if page_id:
        resp = client.pages.update(page_id=page_id, properties=properties)
    else:
        resp = client.pages.create(
            parent={"database_id": database_id},
            properties=properties,
        )
    return resp["id"]
