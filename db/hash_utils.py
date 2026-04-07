"""
hash_utils.py
─────────────
Deterministic application_id generation.

Strategy: SHA-256 of "<lower(company)>|<lower(role)>", take first 16 bytes,
format as a UUID. This means the same company+role always produces the same
UUID across runs, processes, and containers — no DB lookup needed before insert.

If role_title is None or empty, we hash with an empty string for that slot.
(Future: emails with no role_title may be dropped from the pipeline entirely.)
"""

import hashlib
import uuid


def make_application_id(company_name: str, role_title: str | None) -> uuid.UUID:
    """Return a deterministic UUID for a (company, role) pair.

    Args:
        company_name: Name of the company (required, non-empty).
        role_title:   Job title / role. None or empty string both produce the
                      same hash — treated as "no role" bucket.

    Returns:
        A UUID derived from the first 16 bytes of SHA-256(key).

    Example:
        >>> make_application_id("Google", "Software Engineer")
        UUID('...')
        >>> # Same inputs → same UUID (idempotent)
        >>> make_application_id("google ", "software engineer")
        UUID('...')  # same as above after normalisation
    """
    company = (company_name or "").lower().strip()
    role = (role_title or "").lower().strip()
    key = f"{company}|{role}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return uuid.UUID(bytes=digest[:16])
