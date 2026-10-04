"""Forward evidence, compact search attribution, feedback and retention.

Revision ID: 20261003_0010
Revises: 20260722_0009

All new columns are nullable or defaulted. Legacy rows remain unmarked for retention.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20261003_0010"
down_revision = "20260722_0009"
branch_labels = None
depends_on = None


ADDED_COLUMNS = {
    "marketplace_items": [
        sa.Column("marketplace_origin_at", sa.Text()),
        sa.Column("first_seen_at", sa.Text()),
        sa.Column("retention_managed", sa.Integer(), nullable=False, server_default="0"),
    ],
    "user_item_states": [
        sa.Column("first_scored_at", sa.Text()),
        sa.Column("item_type", sa.Text()),
        sa.Column("item_type_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("scorer_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("rules_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("repair_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("resale_hash", sa.Text(), nullable=False, server_default=""),
    ],
    "shared_scan_runs": [sa.Column("retention_managed", sa.Integer(), nullable=False, server_default="0")],
    "shared_scan_searches": [
        sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in (
            "detail_fetch_attempts", "detail_fetch_successes", "detail_fetch_failures",
            "component_count", "needs_data_count", "reject_count", "alert_eligible_count",
        )
    ],
    "shared_scan_results": [
        sa.Column("newly_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_status", sa.Text(), nullable=False, server_default="not_requested"),
        sa.Column("scored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_type", sa.Text(), nullable=False, server_default="ambiguous"),
        sa.Column("tier", sa.Text(), nullable=False, server_default=""),
        sa.Column("needs_data", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_eligible", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notification_status", sa.Text(), nullable=False, server_default=""),
    ],
    "listing_decision_traces": [sa.Column("retention_managed", sa.Integer(), nullable=False, server_default="0")],
    "notification_attempts": [sa.Column("retention_managed", sa.Integer(), nullable=False, server_default="0")],
}


def _create_table_if_missing(name: str, *columns: sa.Column, **kwargs: object) -> None:
    if not sa.inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns in ADDED_COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)

    _create_table_if_missing(
        "user_item_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_item_id", sa.Integer(), sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("scorer_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("rules_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("repair_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("resale_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimated_profit", sa.Float()),
        sa.Column("item_type", sa.Text(), nullable=False, server_default="ambiguous"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "marketplace_item_id", name="uq_user_item_feedback_user_item"),
    )
    _create_table_if_missing(
        "user_item_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_item_id", sa.Integer(), sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("purchase_price", sa.Float()), sa.Column("purchase_tax", sa.Float()),
        sa.Column("inbound_shipping", sa.Float()), sa.Column("purchase_date", sa.Text()),
        sa.Column("actual_repair_type", sa.Text()), sa.Column("parts_cost", sa.Float()),
        sa.Column("other_repair_cost", sa.Float()), sa.Column("sale_date", sa.Text()),
        sa.Column("sale_price", sa.Float()), sa.Column("selling_fees", sa.Float()),
        sa.Column("outbound_shipping", sa.Float()), sa.Column("refund_amount", sa.Float()),
        sa.Column("other_cost", sa.Float()), sa.Column("actual_net_profit", sa.Float()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("scorer_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("rules_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("repair_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("resale_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimated_profit", sa.Float()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "marketplace_item_id", name="uq_user_item_outcomes_user_item"),
    )
    metric_names = (
        "executions", "results_returned", "distinct_listings", "newly_discovered",
        "whole_phone_candidates", "component_listings", "gem_count", "profitable_count",
        "review_count", "needs_data_count", "reject_count", "alert_eligible_count",
        "alerts_sent", "detail_fetch_attempts", "detail_fetch_successes", "detail_fetch_failures",
    )
    _create_table_if_missing(
        "search_daily_rollups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.Text(), nullable=False),
        sa.Column("search_signature", sa.Text(), nullable=False),
        sa.Column("marketplace", sa.Text(), nullable=False),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in metric_names),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("day", "search_signature", "marketplace", name="uq_search_daily_rollups_day_signature"),
    )
    _create_table_if_missing(
        "search_rollup_days",
        sa.Column("day", sa.Text(), primary_key=True),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
    )
    _create_table_if_missing(
        "retention_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dry_run", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text()),
        sa.Column("cutoffs_json", sa.Text(), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    _create_index_if_missing("idx_forward_marketplace_seen", "marketplace_items", ["retention_managed", "first_seen_at"])
    _create_index_if_missing("idx_forward_traces_age", "listing_decision_traces", ["retention_managed", "created_at"])
    _create_index_if_missing("idx_forward_runs_age", "shared_scan_runs", ["retention_managed", "started_at"])
    _create_index_if_missing("idx_forward_notifications_age", "notification_attempts", ["retention_managed", "created_at"])


def downgrade() -> None:
    for index, table in (
        ("idx_forward_notifications_age", "notification_attempts"),
        ("idx_forward_runs_age", "shared_scan_runs"),
        ("idx_forward_traces_age", "listing_decision_traces"),
        ("idx_forward_marketplace_seen", "marketplace_items"),
    ):
        op.drop_index(index, table_name=table)
    for table in ("retention_runs", "search_rollup_days", "search_daily_rollups", "user_item_outcomes", "user_item_feedback"):
        op.drop_table(table)
    for table, columns in reversed(list(ADDED_COLUMNS.items())):
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                batch.drop_column(column.name)
