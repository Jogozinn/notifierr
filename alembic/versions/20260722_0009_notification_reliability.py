"""persist notification fingerprints, retry state, and re-alert settings

Revision ID: 20260722_0009
Revises: 20260722_0008
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260722_0009"
down_revision = "20260722_0008"
branch_labels = None
depends_on = None


ATTEMPT_COLUMNS = (
    sa.Column("status", sa.Text(), nullable=False, server_default="legacy_unclassified"),
    sa.Column("fingerprint", sa.Text(), nullable=False, server_default=""),
    sa.Column("notification_tier", sa.Text(), nullable=False, server_default=""),
    sa.Column("effective_price", sa.Float()),
    sa.Column("expected_profit", sa.Float()),
    sa.Column("expected_roi", sa.Float()),
    sa.Column("principal_damage", sa.Text(), nullable=False, server_default="unknown"),
    sa.Column("availability_state", sa.Text(), nullable=False, server_default="unknown"),
    sa.Column("confidence", sa.Text(), nullable=False, server_default="low"),
    sa.Column("destination_identity", sa.Text(), nullable=False, server_default=""),
    sa.Column("successful_at", sa.Text()),
    sa.Column("prior_success_attempt_id", sa.Integer()),
    sa.Column("fingerprint_match_reason", sa.Text(), nullable=False, server_default=""),
    sa.Column("source", sa.Text(), nullable=False, server_default="live_scan"),
    sa.Column("next_eligible_at", sa.Text()),
    sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("parent_attempt_id", sa.Integer()),
    sa.Column("claimed_at", sa.Text()),
)

SETTING_COLUMNS = (
    sa.Column("meaningful_price_drop_amount", sa.Float(), nullable=False, server_default="20"),
    sa.Column("meaningful_price_drop_percent", sa.Float(), nullable=False, server_default="0.05"),
    sa.Column("meaningful_profit_increase_amount", sa.Float(), nullable=False, server_default="25"),
    sa.Column("meaningful_profit_increase_percent", sa.Float(), nullable=False, server_default="0.15"),
    sa.Column("meaningful_roi_increase", sa.Float(), nullable=False, server_default="0.10"),
    sa.Column("catchup_enabled", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("catchup_batch_size", sa.Integer(), nullable=False, server_default="5"),
    sa.Column("catchup_include_review", sa.Integer(), nullable=False, server_default="0"),
)


def _names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, columns in (("notification_attempts", ATTEMPT_COLUMNS), ("user_notification_settings", SETTING_COLUMNS)):
        existing = _names(table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    op.execute("""
        UPDATE notification_attempts SET status = CASE
          WHEN sent = 1 THEN 'sent'
          WHEN failed = 1 THEN 'failed'
          WHEN deduplicated = 1 THEN 'skipped_duplicate'
          WHEN failure_category = 'review_hourly_limit' THEN 'deferred_rate_limit'
          WHEN skipped = 1 THEN 'skipped'
          ELSE 'pending' END
    """)
    op.execute("UPDATE notification_attempts SET successful_at = updated_at WHERE sent = 1 AND successful_at IS NULL")
    op.execute("""
        UPDATE notification_attempts SET next_eligible_at = updated_at
        WHERE status IN ('failed', 'deferred_rate_limit') AND next_eligible_at IS NULL
    """)
    op.create_index(
        "uq_notification_attempt_pending_item",
        "notification_attempts",
        ["user_id", "item_id", "destination_identity"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND claimed_at IS NOT NULL"),
        sqlite_where=sa.text("status = 'pending' AND claimed_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notification_attempt_pending_item", table_name="notification_attempts")
    for table, columns in (("user_notification_settings", SETTING_COLUMNS), ("notification_attempts", ATTEMPT_COLUMNS)):
        existing = _names(table)
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                if column.name in existing:
                    batch.drop_column(column.name)
