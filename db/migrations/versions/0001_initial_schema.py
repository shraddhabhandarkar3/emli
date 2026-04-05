"""Initial schema — applications and email_events tables.

Revision ID: 0001
Revises: (none — first migration)
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ────────────────────────────────────────────────────
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── applications ────────────────────────────────────────────────────────
    # application_id is a deterministic UUID computed in Python via
    # make_application_id(company_name, role_title) — NOT server-generated.
    op.create_table(
        "applications",
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("role_title", sa.Text(), nullable=True),
        sa.Column("applied_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
    )

    # ── email_events ─────────────────────────────────────────────────────────
    # id is server-generated (gen_random_uuid); gmail_id is the idempotency key.
    op.create_table(
        "email_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("gmail_id", sa.Text(), unique=True, nullable=False),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.application_id"),
            nullable=True,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("role_title", sa.Text(), nullable=True),
        sa.Column(
            "notion_synced",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
    )

    # ── Indexes ──────────────────────────────────────────────────────────────
    # Speed up the common queries Ticket 4 (Notion sync) will run
    op.create_index(
        "ix_email_events_application_id",
        "email_events",
        ["application_id"],
    )
    op.create_index(
        "ix_email_events_notion_synced",
        "email_events",
        ["notion_synced"],
        postgresql_where=sa.text("notion_synced = FALSE"),  # partial index
    )


def downgrade() -> None:
    op.drop_index("ix_email_events_notion_synced", table_name="email_events")
    op.drop_index("ix_email_events_application_id", table_name="email_events")
    op.drop_table("email_events")
    op.drop_table("applications")
