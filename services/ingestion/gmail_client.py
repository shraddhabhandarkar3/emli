"""
gmail_client.py
───────────────
Gmail API wrapper with incremental fetch via the History API.

How incrementality works
────────────────────────
Gmail's History API tracks every inbox change using a monotonically increasing
historyId. We store the latest historyId in GOOGLE_STATE_PATH after every run:

  First run   → messages.list(q="in:inbox newer_than:Nd")
                saves current historyId from getProfile()

  Later runs  → history.list(startHistoryId=saved_id, historyTypes=["messageAdded"])
                only returns messages added since last run
                saves the new historyId from the response

If the historyId has expired (Gmail expires history after ~7 days of inactivity
or ~30 days total), we fall back to a fresh initial fetch automatically.

Public API
──────────
  build_service(creds)         → Gmail API service object
  fetch_new_emails(service)    → list of email dicts:
                                   {gmail_id, subject, sender, date, body_text}
"""

import base64
import json
import logging
import os
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

load_dotenv()

logger = logging.getLogger(__name__)

_GMAIL_QUERY = "in:inbox"
_FETCH_DAYS = int(os.environ.get("GMAIL_FETCH_DAYS", "90"))


# ─────────────────────────────────────────────────────────────────────────────
# HTML → plain text
# ─────────────────────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text converter using only the stdlib."""

    _BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP_TAGS = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        import re
        text = "".join(self._parts)
        # Collapse 3+ blank lines into 2 and strip leading/trailing whitespace
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _strip_html(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.get_text()


# ─────────────────────────────────────────────────────────────────────────────
# Message parsing
# ─────────────────────────────────────────────────────────────────────────────

def _decode(data: str) -> str:
    """Decode a base64url-encoded Gmail message body part."""
    # Gmail sometimes omits padding — add it back
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    """Recursively walk MIME payload to extract the best plain text available.

    Priority: text/plain > text/html (stripped) > nested multipart parts.
    """
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return _decode(data) if data else ""

    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        return _strip_html(_decode(data)) if data else ""

    if mime.startswith("multipart/"):
        parts = payload.get("parts", [])
        # Prefer explicit text/plain first
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return _decode(data)
        # Fall back to HTML
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return _strip_html(_decode(data))
        # Recurse into nested multipart (e.g. multipart/mixed wrapping multipart/alternative)
        for part in parts:
            text = _extract_body(part)
            if text:
                return text

    return ""


def _parse_message(msg: dict) -> dict:
    """Convert a raw Gmail API message object into a flat email dict."""
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }
    return {
        "gmail_id":  msg["id"],
        "subject":   headers.get("subject", ""),
        "sender":    headers.get("from", ""),
        "date":      headers.get("date", ""),
        "body_text": _extract_body(msg.get("payload", {})),
    }


# ─────────────────────────────────────────────────────────────────────────────
# State persistence (historyId)
# ─────────────────────────────────────────────────────────────────────────────

def _state_path() -> Path:
    return Path(os.environ.get("GOOGLE_STATE_PATH", "token/gmail_state.json"))


def _load_state() -> dict:
    path = _state_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
    logger.debug("State saved: %s", state)


# ─────────────────────────────────────────────────────────────────────────────
# Gmail API helpers
# ─────────────────────────────────────────────────────────────────────────────

def build_service(creds: Credentials):
    """Return an authenticated Gmail API v1 service."""
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _get_message(service, msg_id: str, retries: int = 4) -> Optional[dict]:
    """Fetch a single full message with exponential backoff on rate limits."""
    for attempt in range(retries):
        try:
            return (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status in (429, 500, 503):
                wait = 2 ** attempt
                logger.warning("Rate limited — retrying in %ds…", wait)
                time.sleep(wait)
            else:
                raise
    logger.error("Failed to fetch message %s after %d retries", msg_id, retries)
    return None


def _fetch_messages(service, message_ids: list[str]) -> list[dict]:
    """Fetch and parse a list of Gmail message IDs."""
    total = len(message_ids)
    emails = []
    for i, msg_id in enumerate(message_ids, 1):
        if i == 1 or i % 25 == 0 or i == total:
            logger.info("Fetching message %d / %d…", i, total)
        msg = _get_message(service, msg_id)
        if msg:
            emails.append(_parse_message(msg))
    return emails


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def fetch_new_emails(service) -> list[dict]:
    """Return new inbox emails since the last run.

    Uses the Gmail History API for incremental fetches; falls back to a
    date-bounded initial fetch on first run or if historyId has expired.

    Returns a list of dicts: {gmail_id, subject, sender, date, body_text}
    """
    state = _load_state()
    history_id = state.get("historyId")

    if history_id:
        emails, new_history_id = _incremental_fetch(service, history_id)
    else:
        emails, new_history_id = _initial_fetch(service)

    if new_history_id:
        _save_state({"historyId": new_history_id})

    return emails


def _initial_fetch(service) -> tuple[list[dict], str]:
    """Fetch the last GMAIL_FETCH_DAYS days from inbox (first run only)."""
    logger.info("First run — fetching last %d days from inbox…", _FETCH_DAYS)
    query = f"{_GMAIL_QUERY} newer_than:{_FETCH_DAYS}d"

    message_ids: list[str] = []
    page_token: Optional[str] = None

    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        message_ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Capture the current historyId so the next run can be incremental
    profile = service.users().getProfile(userId="me").execute()
    history_id: str = profile["historyId"]

    emails = _fetch_messages(service, message_ids)
    logger.info("Initial fetch: %d emails | historyId=%s", len(emails), history_id)
    return emails, history_id


def _incremental_fetch(service, start_history_id: str) -> tuple[list[dict], str]:
    """Fetch only messages added to inbox since start_history_id."""
    logger.info("Incremental fetch from historyId=%s…", start_history_id)

    message_ids: list[str] = []
    new_history_id = start_history_id
    page_token: Optional[str] = None

    try:
        while True:
            resp = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                    pageToken=page_token,
                )
                .execute()
            )
            # Always capture the latest historyId from the response
            new_history_id = resp.get("historyId", new_history_id)

            for record in resp.get("history", []):
                for added in record.get("messagesAdded", []):
                    message_ids.append(added["message"]["id"])

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    except HttpError as exc:
        if exc.resp.status == 404:
            # historyId expired — transparent fallback to full initial fetch
            logger.warning("historyId expired — falling back to initial fetch…")
            return _initial_fetch(service)
        raise

    emails = _fetch_messages(service, message_ids)
    logger.info(
        "Incremental fetch: %d new email(s) | new historyId=%s",
        len(emails),
        new_history_id,
    )
    return emails, new_history_id
