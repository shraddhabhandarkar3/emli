"""
tests/classifier/test_classifier.py
─────────────────────────────────────
Unit tests for the classifier module — all LLM calls are mocked.

Tests:
  1. Non-job email returns is_job_related=False, no DB write
  2. Stage 2 parses valid JSON into correct ClassificationResult
  3. Stage 2 returns unknown category → falls back to needs_review
  4. Stage 2 missing company_name → sender domain fallback
  5. Stage 2 returns unparseable JSON → needs_review + domain fallback
"""

from unittest.mock import patch

import pytest

from services.classifier.classifier import (
    ClassificationResult,
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
# 1 — Stage 1: non-job email is discarded
# ─────────────────────────────────────────────────────────────────────────────

def test_non_job_email_discarded():
    """Stage 1 returns false → is_job_related=False, no Stage 2 call."""
    stage1_response = {"is_job_related": False}

    with patch("services.classifier.classifier.call_ollama", return_value=stage1_response) as mock:
        result = classify_email(_email(subject="Weekend sale — 50% off everything!"))

    assert result.is_job_related is False
    assert result.category is None
    # Stage 1 only — exactly one LLM call
    assert mock.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2 — Stage 2: valid response parsed correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_valid_stage2_response_parsed():
    """Full two-stage path with a clean LLM response."""
    stage1_response = {"is_job_related": True}
    stage2_response = {
        "category": "interview_scheduled",
        "company_name": "Stripe",
        "role_title": "Data Engineer",
    }

    with patch("services.classifier.classifier.call_ollama",
               side_effect=[stage1_response, stage2_response]):
        result = classify_email(_email(
            subject="Interview confirmation — Stripe",
            sender="recruiting@stripe.com",
        ))

    assert result.is_job_related is True
    assert result.category == "interview_scheduled"
    assert result.company_name == "Stripe"
    assert result.role_title == "Data Engineer"


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Stage 2: unknown category falls back to needs_review
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_category_falls_back_to_needs_review():
    """LLM returns a category not in VALID_CATEGORIES → needs_review."""
    stage1_response = {"is_job_related": True}
    stage2_response = {
        "category": "salary_negotiation",   # not a valid category
        "company_name": "Google",
        "role_title": "SWE",
    }

    with patch("services.classifier.classifier.call_ollama",
               side_effect=[stage1_response, stage2_response]):
        result = classify_email(_email(sender="jobs@google.com"))

    assert result.category == "needs_review"
    assert result.is_job_related is True


# ─────────────────────────────────────────────────────────────────────────────
# 4 — Stage 2: missing company_name uses sender domain
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_company_name_uses_sender_domain():
    """If company_name is empty in LLM response, fall back to sender domain."""
    stage1_response = {"is_job_related": True}
    stage2_response = {
        "category": "rejected",
        "company_name": "",       # empty — LLM didn't extract it
        "role_title": "",
    }

    with patch("services.classifier.classifier.call_ollama",
               side_effect=[stage1_response, stage2_response]):
        result = classify_email(_email(sender="no-reply@greenhouse.io"))

    assert result.company_name == "greenhouse.io"
    assert result.category == "rejected"
    assert result.role_title is None


# ─────────────────────────────────────────────────────────────────────────────
# 5 — Stage 2: malformed/empty JSON falls back gracefully
# ─────────────────────────────────────────────────────────────────────────────

def test_malformed_stage2_response_falls_back():
    """If call_ollama returns {} (parse failure), classifier uses safe defaults."""
    stage1_response = {"is_job_related": True}
    stage2_response = {}   # empty dict — as returned by llm_client on JSON error

    with patch("services.classifier.classifier.call_ollama",
               side_effect=[stage1_response, stage2_response]):
        result = classify_email(_email(sender="talent@meta.com"))

    assert result.is_job_related is True
    assert result.category == "needs_review"
    assert result.company_name == "meta.com"   # sender domain fallback


# ─────────────────────────────────────────────────────────────────────────────
# _sender_domain helper
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sender,expected", [
    ("HR <hr@stripe.com>",          "stripe.com"),
    ("no-reply@greenhouse.io",      "greenhouse.io"),
    ("Workday <x@myworkday.com>",   "myworkday.com"),
    ("nobody",                      "nobody"),
    ("",                            "unknown"),
])
def test_sender_domain_extraction(sender, expected):
    assert _sender_domain(sender) == expected
