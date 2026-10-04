"""Web Push subscriptions and per-device delivery evidence.

Revision ID: 20261004_0011
Revises: 20261003_0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20261004_0011"
down_revision = "20261003_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("user_notification_settings")}
    if "push_enabled" not in columns:
        op.add_column(
            "user_notification_settings",
            sa.Column("push_enabled", sa.Integer(), nullable=False, server_default="1"),
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "push_subscriptions" not in tables:
        op.create_table(
            "push_subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("endpoint_hash", sa.Text(), nullable=False),
            sa.Column("endpoint", sa.Text(), nullable=False),
            sa.Column("p256dh", sa.Text(), nullable=False),
            sa.Column("auth", sa.Text(), nullable=False),
            sa.Column("device_label", sa.Text(), nullable=False, server_default=""),
            sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
            sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_seen_at", sa.Text()),
            sa.Column("last_success_at", sa.Text()),
            sa.Column("last_failure_at", sa.Text()),
            sa.Column("invalidated_at", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("user_id", "endpoint_hash", name="uq_push_subscriptions_user_endpoint"),
        )
        op.create_index("idx_push_subscriptions_user_enabled", "push_subscriptions", ["user_id", "enabled"])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "push_delivery_attempts" not in tables:
        op.create_table(
            "push_delivery_attempts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("push_subscriptions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("item_id", sa.Text()),
            sa.Column("scan_cycle_id", sa.Integer(), sa.ForeignKey("scan_cycles.id", ondelete="SET NULL")),
            sa.Column("notification_tier", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("provider_status", sa.Integer()),
            sa.Column("error_category", sa.Text(), nullable=False, server_default=""),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index("idx_push_delivery_user_created", "push_delivery_attempts", ["user_id", "created_at"])
        op.create_index("idx_push_delivery_subscription", "push_delivery_attempts", ["subscription_id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "push_delivery_attempts" in tables:
        op.drop_index("idx_push_delivery_subscription", table_name="push_delivery_attempts")
        op.drop_index("idx_push_delivery_user_created", table_name="push_delivery_attempts")
        op.drop_table("push_delivery_attempts")
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "push_subscriptions" in tables:
        op.drop_index("idx_push_subscriptions_user_enabled", table_name="push_subscriptions")
        op.drop_table("push_subscriptions")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("user_notification_settings")}
    if "push_enabled" in columns:
        with op.batch_alter_table("user_notification_settings") as batch:
            batch.drop_column("push_enabled")
