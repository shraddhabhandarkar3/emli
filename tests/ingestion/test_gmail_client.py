"""
tests/ingestion/test_gmail_client.py
─────────────────────────────────────
Unit tests for gmail_client.py.

Tests 1-7 : pure functions — no mocks, no I/O, run instantly.
Tests 8-9 : stateful fetch logic — mock the Gmail API service.
"""

import base64
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from services.ingestion.gmail_client import (
    _decode,
    _extract_body,
    _parse_message,
    _strip_html,
    fetch_new_emails,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _b64(text: str) -> str:
    """Encode text as base64url without padding — exactly how Gmail sends it."""
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def _plain_msg(msg_id: str, subject: str, sender: str, body: str) -> dict:
    """Build a minimal Gmail API message object with a text/plain body."""
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From",    "value": sender},
                {"name": "Date",    "value": "Mon, 7 Apr 2026 10:00:00 +0000"},
            ],
            "body": {"data": _b64(body)},
        },
    }


class _Http404Response:
    """Minimal stand-in for an httplib2 response with status 404."""
    status = 404
    reason = "Not Found"


def _build_service(messages: list[dict], profile_history_id: str,
                   history_raises_404: bool = False) -> MagicMock:
    """Return a mock Gmail API service wired up with the given responses.

    Uses `.return_value` chains so the mock behaves correctly regardless of
    which keyword arguments the production code passes.
    """
    svc = MagicMock()
    msg_map = {m["id"]: m for m in messages}

    # ── messages().list() ────────────────────────────────────────────────────
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": mid} for mid in msg_map],
        # no nextPageToken → single page
    }

    # ── getProfile() ─────────────────────────────────────────────────────────
    svc.users.return_value.getProfile.return_value.execute.return_value = {
        "historyId": profile_history_id,
    }

    # ── messages().get(id=...) ───────────────────────────────────────────────
    # Called once per message ID; side_effect lets us return the right payload.
    def _get_side_effect(userId=None, id=None, format=None):
        child = MagicMock()
        child.execute.return_value = msg_map.get(id, {})
        return child

    svc.users.return_value.messages.return_value.get.side_effect = _get_side_effect

    # ── history().list() ─────────────────────────────────────────────────────
    if history_raises_404:
        svc.users.return_value.history.return_value.list.return_value.execute.side_effect = (
            HttpError(resp=_Http404Response(), content=b"")
        )
    else:
        svc.users.return_value.history.return_value.list.return_value.execute.return_value = {
            "historyId": profile_history_id,
            "history": [
                {"messagesAdded": [{"message": {"id": mid}} for mid in msg_map]}
            ],
        }

    return svc


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2 — _strip_html
# ─────────────────────────────────────────────────────────────────────────────

def test_strip_html_removes_basic_tags():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_excludes_script_and_style_content():
    """Script / style content must never reach the LLM classifier."""
    result = _strip_html(
        "<p>Safe text</p>"
        "<script>document.cookie = 'evil'</script>"
        "<style>.hidden { display:none }</style>"
    )
    assert "evil" not in result
    assert "hidden" not in result
    assert "Safe text" in result


# ─────────────────────────────────────────────────────────────────────────────
# 3 — _decode
# ─────────────────────────────────────────────────────────────────────────────

def test_decode_handles_missing_base64_padding():
    """Gmail strips trailing '=' padding — _decode must still work."""
    unpadded = base64.urlsafe_b64encode(b"Hello Gmail").rstrip(b"=").decode()
    assert _decode(unpadded) == "Hello Gmail"


# ─────────────────────────────────────────────────────────────────────────────
# 4-6 — _extract_body
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_body_prefers_plain_text_over_html():
    """When both parts exist, the classifier should receive plain text, not HTML."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html",  "body": {"data": _b64("<p>HTML version</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("Plain text version")}},
        ],
    }
    assert _extract_body(payload) == "Plain text version"


def test_extract_body_falls_back_to_html_when_no_plain_part():
    """Most HR / company emails are HTML-only — must still return usable text."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>HTML only email</p>")}},
        ],
    }
    assert "HTML only email" in _extract_body(payload)


def test_extract_body_recurses_into_nested_multipart():
    """Real-world structure: multipart/mixed › multipart/alternative › text/plain."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("Nested plain text")}},
                ],
            },
        ],
    }
    assert _extract_body(payload) == "Nested plain text"


# ─────────────────────────────────────────────────────────────────────────────
# 7 — _parse_message
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_message_extracts_all_fields():
    msg = _plain_msg("abc123", "You're hired!", "hr@acme.com", "Congratulations!")
    parsed = _parse_message(msg)

    assert parsed["gmail_id"]  == "abc123"
    assert parsed["subject"]   == "You're hired!"
    assert parsed["sender"]    == "hr@acme.com"
    assert parsed["date"]      == "Mon, 7 Apr 2026 10:00:00 +0000"
    assert "Congratulations"   in parsed["body_text"]


# ─────────────────────────────────────────────────────────────────────────────
# 8 — first run saves historyId
# ─────────────────────────────────────────────────────────────────────────────

def test_first_run_saves_history_id(tmp_path, monkeypatch):
    """On first run (no state file), historyId must be persisted after fetch
    so the next run can use the History API instead of re-fetching everything."""
    monkeypatch.setenv("GOOGLE_STATE_PATH", str(tmp_path / "state.json"))

    msg = _plain_msg("msg1", "Offer Letter", "stripe@stripe.com", "Congrats!")
    svc = _build_service(messages=[msg], profile_history_id="99999")

    emails = fetch_new_emails(svc)

    assert len(emails) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["historyId"] == "99999"


# ─────────────────────────────────────────────────────────────────────────────
# 9 — expired historyId falls back gracefully
# ─────────────────────────────────────────────────────────────────────────────

def test_expired_history_id_falls_back_to_initial_fetch(tmp_path, monkeypatch):
    """Gmail expires history after ~30 days. On a 404, we must transparently
    fall back to messages.list so the pipeline never requires manual recovery."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"historyId": "EXPIRED_ID"}))
    monkeypatch.setenv("GOOGLE_STATE_PATH", str(state_path))

    msg = _plain_msg("msg2", "Interview", "google@google.com", "Let's chat!")
    svc = _build_service(
        messages=[msg],
        profile_history_id="11111",
        history_raises_404=True,
    )

    emails = fetch_new_emails(svc)

    assert len(emails) == 1
    # historyId must be updated to the new ID from the fallback fetch
    state = json.loads(state_path.read_text())
    assert state["historyId"] == "11111"
