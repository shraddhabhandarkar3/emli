"""
tests/classifier/test_classifier.py
─────────────────────────────────────
Unit tests for the classifier module — all LLM calls are mocked.

Tests:
  1. Non-job email returns is_job_related=False, no Stage 2 call
  2. Stage 2 parses valid JSON into correct ClassificationResult
  3. Stage 2 returns unknown category → falls back to needs_review
  4. Stage 2 missing company_name → sender domain fallback
  5. Stage 2 returns empty dict (parse failure) → needs_review + domain fallback
  6. _sender_domain: parametrised extraction cases
"""

from unittest.mock import patch

import pytest

from services.classifier.classifier import (
    _sender_domain,
    classify_email,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _email(subject="Test subject", sender="hr@acme.com", body="Some body text"):
    return {
        "gmail_id": "abc123",
        "subject": subject,
        "sender": sender,
        "body_text": body,
        "date": "Mon, 7 Apr 2026 10:00:00 +0000",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_non_job_email_discarded():
    """Stage 1 returns false → is_job_related=False, Stage 2 never called."""
    with patch("services.classifier.classifier.call_llm",
               return_value={"is_job_related": False}) as mock:
        result = classify_email(_email(subject="Weekend sale — 50% off!"))

    assert result.is_job_related is False
    assert result.category is None
    assert mock.call_count == 1   # only Stage 1


def test_valid_stage2_response_parsed():
    """Full two-stage path with a clean LLM response → correct result."""
    with patch("services.classifier.classifier.call_llm", side_effect=[
        {"is_job_related": True},
        {"category": "interview_scheduled", "company_name": "Stripe", "role_title": "Data Engineer"},
    ]):
        result = classify_email(_email(subject="Interview — Stripe", sender="recruiting@stripe.com"))

    assert result.is_job_related is True
    assert result.category == "interview_scheduled"
    assert result.company_name == "Stripe"
    assert result.role_title == "Data Engineer"


def test_unknown_category_falls_back_to_needs_review():
    """LLM returns unrecognised category → needs_review."""
    with patch("services.classifier.classifier.call_llm", side_effect=[
        {"is_job_related": True},
        {"category": "salary_negotiation", "company_name": "Google", "role_title": "SWE"},
    ]):
        result = classify_email(_email(sender="jobs@google.com"))

    assert result.category == "needs_review"
    assert result.is_job_related is True


def test_missing_company_name_uses_sender_domain():
    """Empty company_name from LLM → sender domain used (NOT NULL constraint)."""
    with patch("services.classifier.classifier.call_llm", side_effect=[
        {"is_job_related": True},
        {"category": "rejected", "company_name": "", "role_title": ""},
    ]):
        result = classify_email(_email(sender="no-reply@greenhouse.io"))

    assert result.company_name == "greenhouse.io"
    assert result.category == "rejected"
    assert result.role_title is None


def test_malformed_stage2_response_falls_back():
    """call_llm returns {} (JSON parse failure) → safe defaults applied."""
    with patch("services.classifier.classifier.call_llm", side_effect=[
        {"is_job_related": True},
        {},   # empty dict — llm_client returns this on JSONDecodeError
    ]):
        result = classify_email(_email(sender="talent@meta.com"))

    assert result.is_job_related is True
    assert result.category == "needs_review"
    assert result.company_name == "meta.com"


@pytest.mark.parametrize("sender,expected", [
    ("HR <hr@stripe.com>",        "stripe.com"),
    ("no-reply@greenhouse.io",    "greenhouse.io"),
    ("Workday <x@myworkday.com>", "myworkday.com"),
    ("nobody",                    "nobody"),
    ("",                          "unknown"),
])
def test_sender_domain_extraction(sender, expected):
    assert _sender_domain(sender) == expected
