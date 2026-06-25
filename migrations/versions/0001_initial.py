"""initial schema: tenants, calls, memories

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-14

Mirrors gradphone.db.metadata. Timestamps are TEXT (ISO-8601) and booleans are
INTEGER, matching the original SQLite schema so existing data formats are
unchanged across backends.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Integer(), server_default=sa.text("1")),
        sa.Column("custom_calls_per_day", sa.Integer(), nullable=True),
        sa.Column("voice_id", sa.Text(), nullable=True),
        sa.Column("voice_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_table(
        "calls",
        sa.Column("room", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("twilio_call_sid", sa.Text(), nullable=True),
        sa.Column("destination", sa.Text(), nullable=True),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("business_name", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("ended_at", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("twilio_call_status", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_calls_tenant_started", "calls", ["tenant_id", sa.text("started_at DESC")]
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("room", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_memories_tenant", "memories", ["tenant_id", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("idx_memories_tenant", table_name="memories")
    op.drop_table("memories")
    op.drop_index("idx_calls_tenant_started", table_name="calls")
    op.drop_table("calls")
    op.drop_table("tenants")
