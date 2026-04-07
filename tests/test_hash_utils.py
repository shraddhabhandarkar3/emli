"""
tests/test_hash_utils.py
─────────────────────────
Smoke tests for make_application_id — no DB connection required.
"""

import uuid
import pytest
from db.hash_utils import make_application_id


def test_same_inputs_same_uuid():
    """Determinism: same company+role → always the same UUID."""
    a = make_application_id("Google", "Software Engineer")
    b = make_application_id("Google", "Software Engineer")
    assert a == b


def test_normalisation_case_whitespace():
    """Normalisation: case and surrounding whitespace are ignored."""
    a = make_application_id("Google", "Software Engineer")
    b = make_application_id("  google  ", "  software engineer  ")
    assert a == b


def test_different_company_different_uuid():
    """Distinct companies produce distinct UUIDs."""
    a = make_application_id("Google", "Software Engineer")
    b = make_application_id("Meta", "Software Engineer")
    assert a != b


def test_different_role_different_uuid():
    """Distinct roles at the same company produce distinct UUIDs."""
    a = make_application_id("Google", "Software Engineer")
    b = make_application_id("Google", "Data Scientist")
    assert a != b


def test_none_role_title_becomes_empty_string():
    """None and empty string role_title are treated identically."""
    a = make_application_id("Stripe", None)
    b = make_application_id("Stripe", "")
    assert a == b


def test_returns_uuid_type():
    """Return type is always uuid.UUID."""
    result = make_application_id("Airbnb", "Backend Engineer")
    assert isinstance(result, uuid.UUID)


def test_no_role_still_unique_per_company():
    """Two companies with no role produce different UUIDs."""
    a = make_application_id("Uber", None)
    b = make_application_id("Lyft", None)
    assert a != b
