"""
tests/etl/test_applications_builder.py
────────────────────────────────────────
Unit tests for the ETL applications builder logic.
All DB sessions are mocked — no real database needed.

Tests:
  1. Single 'applied' event → status='applied', applied_date set
  2. 'applied' + 'rejected' → status='rejected' (higher priority wins)
  3. 'applied' + 'interview_scheduled' → status='interview_scheduled'
  4. 'offer_extended' beats everything including 'rejected'
  5. No 'applied' event → applied_date falls back to oldest event date
  6. Empty event list → returns 0, no upsert called
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest

from services.etl.applications_builder import build_applications, _priority


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_event(category: str, company: str = "Acme", role: str = "Analyst",
                created_at: datetime | None = None) -> MagicMock:
    """Create a mock EmailEvent with the given fields."""
    event = MagicMock()
    event.application_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{company}{role}")
    event.category = category
    event.company_name = company
    event.role_title = role
    event.created_at = created_at or datetime(2024, 1, 15, tzinfo=timezone.utc)
    return event


# ─────────────────────────────────────────────────────────────────────────────
# Priority helper tests
# ─────────────────────────────────────────────────────────────────────────────

def test_priority_ordering():
    """offer_extended should have the lowest priority number (highest rank)."""
    assert _priority("offer_extended") < _priority("rejected")
    assert _priority("rejected") < _priority("interview_completed")
    assert _priority("interview_completed") < _priority("interview_scheduled")
    assert _priority("interview_scheduled") < _priority("assessment")
    assert _priority("assessment") < _priority("applied")
    assert _priority("applied") < _priority("needs_review")


def test_unknown_category_gets_lowest_priority():
    assert _priority("some_future_label") == 99


# ─────────────────────────────────────────────────────────────────────────────
# build_applications tests
# ─────────────────────────────────────────────────────────────────────────────

@patch("services.etl.applications_builder.upsert_application")
def test_single_applied_event(mock_upsert):
    """One 'applied' event → status=applied, applied_date derived from event."""
    session = MagicMock()
    event = _make_event("applied", created_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
    session.query.return_value.all.return_value = [event]

    result = build_applications(session)

    assert result == 1
    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args.kwargs
    assert call_kwargs["status"] == "applied"
    assert call_kwargs["applied_date"].year == 2024
    assert call_kwargs["applied_date"].month == 3


@patch("services.etl.applications_builder.upsert_application")
def test_rejected_beats_applied(mock_upsert):
    """'rejected' event takes priority over 'applied' for the same application."""
    session = MagicMock()
    app_id = uuid.uuid5(uuid.NAMESPACE_DNS, "AcmeAnalyst")
    
    applied_event = _make_event("applied", created_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
    rejected_event = _make_event("rejected", created_at=datetime(2024, 3, 10, tzinfo=timezone.utc))
    # Same app_id so they group together
    applied_event.application_id = app_id
    rejected_event.application_id = app_id

    session.query.return_value.all.return_value = [applied_event, rejected_event]

    result = build_applications(session)

    assert result == 1  # grouped into one application
    call_kwargs = mock_upsert.call_args.kwargs
    assert call_kwargs["status"] == "rejected"


@patch("services.etl.applications_builder.upsert_application")
def test_offer_beats_rejected(mock_upsert):
    """'offer_extended' should beat 'rejected' (highest priority)."""
    session = MagicMock()
    app_id = uuid.uuid5(uuid.NAMESPACE_DNS, "AcmeAnalyst")

    rejected_event = _make_event("rejected", created_at=datetime(2024, 3, 5, tzinfo=timezone.utc))
    offer_event = _make_event("offer_extended", created_at=datetime(2024, 3, 12, tzinfo=timezone.utc))
    rejected_event.application_id = app_id
    offer_event.application_id = app_id

    session.query.return_value.all.return_value = [rejected_event, offer_event]

    build_applications(session)
    call_kwargs = mock_upsert.call_args.kwargs
    assert call_kwargs["status"] == "offer_extended"


@patch("services.etl.applications_builder.upsert_application")
def test_no_applied_event_uses_oldest_date(mock_upsert):
    """When no 'applied' event exists, applied_date falls back to oldest event."""
    session = MagicMock()
    app_id = uuid.uuid5(uuid.NAMESPACE_DNS, "AcmeAnalyst")

    old_event = _make_event("needs_review", created_at=datetime(2024, 1, 5, tzinfo=timezone.utc))
    new_event = _make_event("needs_review", created_at=datetime(2024, 2, 20, tzinfo=timezone.utc))
    old_event.application_id = app_id
    new_event.application_id = app_id

    session.query.return_value.all.return_value = [old_event, new_event]

    build_applications(session)
    call_kwargs = mock_upsert.call_args.kwargs
    # applied_date should be the oldest event: Jan 5
    assert call_kwargs["applied_date"].month == 1
    assert call_kwargs["applied_date"].day == 5


@patch("services.etl.applications_builder.upsert_application")
def test_empty_events_returns_zero(mock_upsert):
    """No events → returns 0 and never calls upsert."""
    session = MagicMock()
    session.query.return_value.all.return_value = []

    result = build_applications(session)

    assert result == 0
    mock_upsert.assert_not_called()


@patch("services.etl.applications_builder.upsert_application")
def test_multiple_applications_grouped_separately(mock_upsert):
    """Events from two different companies are upserted as separate applications."""
    session = MagicMock()

    stripe_event = _make_event("applied", company="Stripe", role="Engineer")
    google_event = _make_event("rejected", company="Google", role="Analyst")
    # Different application_ids because different company+role combos
    stripe_event.application_id = uuid.uuid5(uuid.NAMESPACE_DNS, "StripeEngineer")
    google_event.application_id = uuid.uuid5(uuid.NAMESPACE_DNS, "GoogleAnalyst")

    session.query.return_value.all.return_value = [stripe_event, google_event]

    result = build_applications(session)

    assert result == 2
    assert mock_upsert.call_count == 2
