"""
notion_client.py
─────────────────
Thin wrapper around the Notion SDK.

Responsibilities:
  - ensure_schema   : guarantee all 7 required properties exist on the DB
  - find_page       : query Notion for a matching row by Company + Role
  - upsert_page     : create or patch a Notion page from an application payload
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

load_dotenv()

logger = logging.getLogger(__name__)

NOTION_TOKEN: str = os.environ.get("NOTION_TOKEN", "")

# ── Properties the database must have (Title 'Company' is always present) ───
_REQUIRED_PROPERTIES: dict[str, Any] = {
    "Role":           {"rich_text": {}},
    "Status":         {"select": {}},
    "Applied Date":   {"date": {}},
    "Last Activity":  {"date": {}},
    "Email Count":    {"number": {"format": "number"}},
    "Needs Review":   {"checkbox": {}},
}


class NotionClientError(Exception):
    """Raised when a Notion API call fails unrecoverably."""


def _get_client() -> Client:
    if not NOTION_TOKEN:
        raise NotionClientError("NOTION_TOKEN is missing from environment.")
    return Client(auth=NOTION_TOKEN)


# ─────────────────────────────────────────────────────────────────────────────
# Schema management
# ─────────────────────────────────────────────────────────────────────────────

def ensure_schema(database_id: str) -> None:
    """Create any missing properties and rename the title column to 'Company'.

    Notion databases are created with a default title property called 'Name'.
    This function renames it to 'Company' and adds all other required properties
    in a single PATCH call. Safe to call on every sync — it is a no-op if the
    schema is already correct.
    """
    client = _get_client()
    try:
        db = client.databases.retrieve(database_id=database_id)
    except APIResponseError as exc:
        raise NotionClientError(f"Cannot retrieve Notion database: {exc}") from exc

    existing: dict = db.get("properties", {})
    existing_names = set(existing.keys())
    updates: dict = {}

    # ── Step 1: Rename the title column to "Company" if needed ───────────────
    title_prop_name = next(
        (k for k, v in existing.items() if v.get("type") == "title"),
        None,
    )
    if title_prop_name and title_prop_name != "Company":
        logger.info("Renaming title property '%s' → 'Company'", title_prop_name)
        updates[title_prop_name] = {"name": "Company"}
        # Mark "Company" as now present so we don't try to add it separately
        existing_names.add("Company")

    # ── Step 2: Add missing non-title properties ──────────────────────────────
    for name, schema in _REQUIRED_PROPERTIES.items():
        if name not in existing_names:
            updates[name] = schema

    if not updates:
        logger.debug("Notion schema is up to date — no changes needed.")
        return

    logger.info("Updating Notion schema: %s", list(updates.keys()))
    try:
        client.databases.update(database_id=database_id, properties=updates)
    except APIResponseError as exc:
        raise NotionClientError(f"Failed to update Notion schema: {exc}") from exc



# ─────────────────────────────────────────────────────────────────────────────
# Page lookup
# ─────────────────────────────────────────────────────────────────────────────

def find_page(database_id: str, company_name: str, role_title: Optional[str]) -> Optional[str]:
    """Return the Notion page_id if a matching row exists, else None.

    Matches on Company (title) AND Role (rich_text).
    """
    client = _get_client()

    filters: list[dict] = [
        {"property": "Company", "title": {"equals": company_name}},
    ]
    if role_title:
        filters.append({"property": "Role", "rich_text": {"equals": role_title}})

    try:
        resp = client.databases.query(
            database_id=database_id,
            filter={"and": filters} if len(filters) > 1 else filters[0],
        )
    except APIResponseError as exc:
        logger.warning("Notion query failed for %s / %s: %s", company_name, role_title, exc)
        return None

    results = resp.get("results", [])
    return results[0]["id"] if results else None


# ─────────────────────────────────────────────────────────────────────────────
# Page upsert
# ─────────────────────────────────────────────────────────────────────────────

def _build_properties(payload: dict) -> dict:
    """Convert an application payload dict into Notion property format."""
    props: dict[str, Any] = {
        "Company": {
            "title": [{"text": {"content": payload["company_name"] or ""}}]
        },
        "Role": {
            "rich_text": [{"text": {"content": payload["role_title"] or ""}}]
        },
        "Status": {
            "select": {"name": payload["category"] or "needs_review"}
        },
        "Email Count": {
            "number": payload.get("email_count", 0)
        },
        "Needs Review": {
            "checkbox": bool(payload.get("needs_review", False))
        },
    }

    applied = payload.get("applied_date")
    if applied:
        props["Applied Date"] = {"date": {"start": str(applied)}}

    last_activity = payload.get("last_activity")
    if last_activity:
        # last_activity is a datetime — convert to date string
        if hasattr(last_activity, "date"):
            last_activity = last_activity.date()
        props["Last Activity"] = {"date": {"start": str(last_activity)}}

    return props


def upsert_page(database_id: str, payload: dict, page_id: Optional[str] = None) -> str:
    """Create or update a Notion page.

    Args:
        database_id: Target Notion database.
        payload:     Application data dict (from get_applications_with_stats).
        page_id:     If provided, patch this existing page. Otherwise create new.

    Returns:
        The Notion page_id (useful for logging).

    Raises:
        NotionClientError on API failure.
    """
    client = _get_client()
    properties = _build_properties(payload)

    try:
        if page_id:
            resp = client.pages.update(page_id=page_id, properties=properties)
        else:
            resp = client.pages.create(
                parent={"database_id": database_id},
                properties=properties,
            )
        return resp["id"]
    except APIResponseError as exc:
        raise NotionClientError(
            f"Failed to upsert page for {payload.get('company_name')}: {exc}"
        ) from exc
