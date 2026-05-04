"""
tests/notion_sync/test_sync_job.py
────────────────────────────────────
Unit tests for the Notion sync orchestration.
All Notion API calls and DB queries are mocked.

Tests:
  1. New application → upsert_page called with page_id=None (create)
  2. Existing application → upsert_page called with existing page_id (update)
  3. Notion API failure → error logged, notion_synced NOT marked, continues
  4. get_applications_with_stats → correct aggregation (email_count, needs_review, last_activity)
  5. Empty applications list → returns (0, 0)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_app(
    company: str = "Stripe",
    role: str = "Data Engineer",
    category: str = "applied",
    email_count: int = 1,
    needs_review: bool = False,
) -> dict:
    return {
        "application_id": uuid.uuid4(),
        "company_name": company,
        "role_title": role,
        "category": category,
        "applied_date": None,
        "last_activity": datetime(2024, 4, 1, tzinfo=timezone.utc),
        "email_count": email_count,
        "needs_review": needs_review,
        "event_ids": [uuid.uuid4()],
    }


# ─────────────────────────────────────────────────────────────────────────────
# sync_job tests
# ─────────────────────────────────────────────────────────────────────────────

@patch("services.notion_sync.sync_job.mark_notion_synced")
@patch("services.notion_sync.sync_job.upsert_page", return_value="new-page-id")
@patch("services.notion_sync.sync_job.find_page", return_value=None)  # not found → create
@patch("services.notion_sync.sync_job.ensure_schema")
@patch("services.notion_sync.sync_job.get_applications_with_stats")
def test_new_application_creates_page(
    mock_get_apps, mock_ensure, mock_find, mock_upsert, mock_mark
):
    """When no Notion page exists for the application, create a new one."""
    session = MagicMock()
    mock_get_apps.return_value = [_make_app()]

    from services.notion_sync.sync_job import run_sync
    import os
    with patch.dict(os.environ, {"NOTION_DATABASE_ID": "db-123"}):
        synced, failed = run_sync(session)

    assert synced == 1
    assert failed == 0
    # upsert_page called with page_id=None (create path)
    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args
    assert call_kwargs.kwargs.get("page_id") is None
    mock_mark.assert_called_once()


@patch("services.notion_sync.sync_job.mark_notion_synced")
@patch("services.notion_sync.sync_job.upsert_page", return_value="existing-page-id")
@patch("services.notion_sync.sync_job.find_page", return_value="existing-page-id")  # found → update
@patch("services.notion_sync.sync_job.ensure_schema")
@patch("services.notion_sync.sync_job.get_applications_with_stats")
def test_existing_application_updates_page(
    mock_get_apps, mock_ensure, mock_find, mock_upsert, mock_mark
):
    """When a Notion page already exists, it should be patched, not created."""
    session = MagicMock()
    mock_get_apps.return_value = [_make_app()]

    from services.notion_sync.sync_job import run_sync
    import os
    with patch.dict(os.environ, {"NOTION_DATABASE_ID": "db-123"}):
        synced, failed = run_sync(session)

    assert synced == 1
    assert failed == 0
    # upsert_page called with the existing page_id (update path)
    call_kwargs = mock_upsert.call_args
    assert call_kwargs.kwargs.get("page_id") == "existing-page-id"


@patch("services.notion_sync.sync_job.mark_notion_synced")
@patch("services.notion_sync.sync_job.upsert_page",
       side_effect=Exception("Notion 500"))  # simulate API failure
@patch("services.notion_sync.sync_job.find_page", return_value=None)
@patch("services.notion_sync.sync_job.ensure_schema")
@patch("services.notion_sync.sync_job.get_applications_with_stats")
def test_notion_failure_does_not_mark_synced(
    mock_get_apps, mock_ensure, mock_find, mock_upsert, mock_mark
):
    """On API failure, notion_synced must NOT be marked and script continues."""
    session = MagicMock()
    mock_get_apps.return_value = [_make_app("FailCo"), _make_app("OkCo")]
    # Second call succeeds
    mock_upsert.side_effect = [Exception("500"), "ok-page-id"]

    from services.notion_sync.sync_job import run_sync
    import os
    with patch.dict(os.environ, {"NOTION_DATABASE_ID": "db-123"}):
        synced, failed = run_sync(session)

    assert synced == 1
    assert failed == 1
    # mark_notion_synced should only have been called once (for OkCo)
    assert mock_mark.call_count == 1


@patch("services.notion_sync.sync_job.mark_notion_synced")
@patch("services.notion_sync.sync_job.upsert_page")
@patch("services.notion_sync.sync_job.find_page")
@patch("services.notion_sync.sync_job.ensure_schema")
@patch("services.notion_sync.sync_job.get_applications_with_stats", return_value=[])
def test_empty_applications_returns_zero(
    mock_get_apps, mock_ensure, mock_find, mock_upsert, mock_mark
):
    """No applications → (0, 0) and no API calls."""
    from services.notion_sync.sync_job import run_sync
    import os
    with patch.dict(os.environ, {"NOTION_DATABASE_ID": "db-123"}):
        synced, failed = run_sync(MagicMock())

    assert synced == 0
    assert failed == 0
    mock_upsert.assert_not_called()
    mock_mark.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# get_applications_with_stats tests
# ─────────────────────────────────────────────────────────────────────────────

def test_get_applications_with_stats_aggregation():
    """Verify email_count, needs_review, and last_activity are computed correctly."""
    from db.repository import get_applications_with_stats
    from db.models import Application, EmailEvent

    app = MagicMock(spec=Application)
    app.application_id = uuid.uuid4()
    app.company_name = "Acme"
    app.role_title = "Analyst"
    app.category = "rejected"
    app.applied_date = None

    dt_old = datetime(2024, 1, 1, tzinfo=timezone.utc)
    dt_new = datetime(2024, 3, 15, tzinfo=timezone.utc)

    event1 = MagicMock(spec=EmailEvent)
    event1.application_id = app.application_id
    event1.category = "applied"
    event1.created_at = dt_old
    event1.id = uuid.uuid4()

    event2 = MagicMock(spec=EmailEvent)
    event2.application_id = app.application_id
    event2.category = "needs_review"
    event2.created_at = dt_new
    event2.id = uuid.uuid4()

    session = MagicMock()

    # query(Application).all() → [app]
    # query(EmailEvent).filter(...).all() → [event1, event2]
    def query_side_effect(model):
        mock_q = MagicMock()
        if model is Application:
            mock_q.all.return_value = [app]
        else:
            mock_q.filter.return_value.all.return_value = [event1, event2]
        return mock_q

    session.query.side_effect = query_side_effect

    result = get_applications_with_stats(session)

    assert len(result) == 1
    stats = result[0]
    assert stats["email_count"] == 2
    assert stats["needs_review"] is True
    assert stats["last_activity"] == dt_new
    assert len(stats["event_ids"]) == 2
