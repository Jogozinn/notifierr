"""add actionable tiers, detail refresh evidence, query yield, and feedback

Revision ID: 20260722_0008
Revises: 20260717_0007
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260722_0008"
down_revision = "20260717_0007"
branch_labels = None
depends_on = None


NOTIFICATION_COLUMNS = (
    sa.Column("send_gem_immediately", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("send_profitable_immediately", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("review_delivery_mode", sa.Text(), nullable=False, server_default="immediate"),
    sa.Column("max_review_alerts_per_hour", sa.Integer(), nullable=False, server_default="2"),
    sa.Column("duplicate_suppression_hours", sa.Integer(), nullable=False, server_default="72"),
    sa.Column("gem_min_expected_profit", sa.Float(), nullable=False, server_default="75"),
    sa.Column("profitable_min_expected_profit", sa.Float(), nullable=False, server_default="50"),
    sa.Column("review_min_expected_profit", sa.Float(), nullable=False, server_default="25"),
    sa.Column("review_min_upside_profit", sa.Float(), nullable=False, server_default="60"),
    sa.Column("gem_min_roi", sa.Float(), nullable=False, server_default="0.25"),
    sa.Column("profitable_min_roi", sa.Float(), nullable=False, server_default="0.15"),
    sa.Column("review_min_roi", sa.Float(), nullable=False, server_default="0.05"),
    sa.Column("max_listing_age_minutes", sa.Integer(), nullable=False, server_default="360"),
)

DETAIL_COLUMNS = (
    sa.Column("detail_fetch_attempted_at", sa.Text()),
    sa.Column("detail_fetch_status", sa.Text(), nullable=False, server_default="not_requested"),
    sa.Column("detail_fetch_reason", sa.Text(), nullable=False, server_default=""),
    sa.Column("detail_fetch_recovered_fields", sa.Text(), nullable=False, server_default="[]"),
    sa.Column("detail_fetch_failure_reason", sa.Text(), nullable=False, server_default=""),
    sa.Column("detail_fetch_retry_after", sa.Text()),
)

SEARCH_COLUMNS = (
    sa.Column("unique_new_items", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("duplicate_items", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("viable_whole_phones", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("gem_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("profitable_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
)


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = _column_names(table_name)
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def upgrade() -> None:
    _add_columns("user_notification_settings", NOTIFICATION_COLUMNS)
    _add_columns("marketplace_items", DETAIL_COLUMNS)
    _add_columns("shared_scan_searches", SEARCH_COLUMNS)
    if "feedback_code" not in _column_names("user_item_corrections"):
        op.add_column(
            "user_item_corrections",
            sa.Column("feedback_code", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    for table_name, columns in (
        ("shared_scan_searches", SEARCH_COLUMNS),
        ("marketplace_items", DETAIL_COLUMNS),
        ("user_notification_settings", NOTIFICATION_COLUMNS),
    ):
        existing = _column_names(table_name)
        for column in reversed(columns):
            if column.name in existing:
                op.drop_column(table_name, column.name)
    if "feedback_code" in _column_names("user_item_corrections"):
        op.drop_column("user_item_corrections", "feedback_code")
