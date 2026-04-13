"""
classifier.py
─────────────
Two-stage email classification pipeline.

Stage 1 — Relevance:  Is this email job-application related?  (yes/no)
Stage 2 — Extraction: If yes, classify category + extract company/role.

Design:
  - Both stages call Ollama with format="json" — no free-form text.
  - Uses <<placeholder>> templates to safely interpolate into prompts that
    contain JSON examples (avoids Python str.format() brace conflicts).
  - Never raises on bad LLM output — malformed responses → needs_review.
  - company_name falls back to sender domain if LLM returns empty string
    (DB enforces NOT NULL on that column).
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from services.classifier.llm_client import call_llm, LLMUnavailableError

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

VALID_CATEGORIES = frozenset({
    "applied",
    "interview_scheduled",
    "interview_completed",
    "assessment",
    "offer_extended",
    "rejected",
    "needs_review",
})

# Body character limits
_STAGE1_BODY_LIMIT = 6000
_STAGE2_BODY_LIMIT = 10000


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    is_job_related: bool
    category: Optional[str]    # None only when is_job_related=False
    company_name: str           # Always non-empty (sender domain fallback)
    role_title: Optional[str]  # None if not mentioned in email


def classify_email(email: dict) -> ClassificationResult:
    """Run two-stage classification on a raw email dict from gmail_client.

    Args:
        email: dict with keys gmail_id, subject, sender, date, body_text.

    Returns:
        ClassificationResult — always populated, never raises.

    Raises:
        LLMUnavailableError — propagated so the caller can decide retry strategy.
    """
    subject   = email.get("subject", "")
    sender    = email.get("sender", "")
    body_text = email.get("body_text", "")

    # Stage 1: is it relevant?
    is_relevant = _stage1_relevance(subject, sender, body_text)
    if not is_relevant:
        logger.debug("Stage 1: NOT job-related — %r", subject[:80])
        return ClassificationResult(
            is_job_related=False,
            category=None,
            company_name=_sender_domain(sender),
            role_title=None,
        )

    logger.debug("Stage 1: job-related — proceeding to Stage 2")
    return _stage2_classify(subject, sender, body_text)


# ─────────────────────────────────────────────────────────────────────────────
# Stage implementations
# ─────────────────────────────────────────────────────────────────────────────

def _stage1_relevance(subject: str, sender: str, body: str) -> bool:
    prompt = _render_prompt(
        "stage1.txt",
        subject=subject,
        sender=sender,
        body=body[:_STAGE1_BODY_LIMIT],
    )
    result = call_llm(prompt)  # raises LLMUnavailableError if down
    return bool(result.get("is_job_related", False))


def _stage2_classify(subject: str, sender: str, body: str) -> ClassificationResult:
    prompt = _render_prompt(
        "stage2.txt",
        subject=subject,
        sender=sender,
        body=body[:_STAGE2_BODY_LIMIT],
    )
    result = call_llm(prompt)  # raises LLMUnavailableError if down

    # ── Validate category ─────────────────────────────────────────────────────
    # The new prompt uses "label" instead of "category"
    category = (result.get("label") or result.get("category") or "needs_review").strip().lower()
    
    # Common LLM mapping errors
    aliases = {
        "rejection": "rejected",
        "offer": "offer_extended",
        "application": "applied",
        "interview": "interview_scheduled"
    }
    category = aliases.get(category, category)

    if category not in VALID_CATEGORIES:
        logger.warning("Unknown category from LLM: %r → needs_review", category)
        category = "needs_review"

    # ── Extract company + role ────────────────────────────────────────────────
    company_name = (result.get("company_name") or "").strip()
    role_title   = (result.get("role_title")   or "").strip() or None

    # company_name is NOT NULL in DB — fall back to sender domain
    if not company_name:
        company_name = _sender_domain(sender)
        logger.debug("company_name not extracted — using sender domain: %r", company_name)

    logger.info(
        "Stage 2: category=%r company=%r role=%r",
        category, company_name, role_title,
    )
    return ClassificationResult(
        is_job_related=True,
        category=category,
        company_name=company_name,
        role_title=role_title,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _render_prompt(filename: str, **kwargs: str) -> str:
    """Load a prompt file and replace <<key>> placeholders.

    Using <<key>> instead of {key} avoids conflicts with JSON curly braces
    that appear in the prompt examples.
    """
    template = (_PROMPTS_DIR / filename).read_text()
    for key, value in kwargs.items():
        template = template.replace(f"<<{key}>>", value)
    return template


def _sender_domain(sender: str) -> str:
    """Extract the domain from a sender string as a company_name fallback.

    Examples:
        'HR Team <hr@stripe.com>'     → 'stripe.com'
        'no-reply@greenhouse.io'      → 'greenhouse.io'
        'Workday <extraspace@myworkday.com>' → 'myworkday.com'
    """
    match = re.search(r"@([\w.-]+)", sender)
    return match.group(1) if match else (sender.strip() or "unknown")
