"""add scan lease takeover and abandoned-cycle recovery metadata

Revision ID: 20260716_0006
Revises: 20260711_0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0006"
down_revision = "20260711_0005"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = {
        "worker_id": sa.Column("worker_id", sa.Text(), nullable=False, server_default=""),
        "abandoned_at": sa.Column("abandoned_at", sa.Text()),
        "abandoned_by_worker_id": sa.Column(
            "abandoned_by_worker_id", sa.Text(), nullable=False, server_default=""
        ),
        "abandonment_reason": sa.Column(
            "abandonment_reason", sa.Text(), nullable=False, server_default=""
        ),
        "partial_trace_count": sa.Column(
            "partial_trace_count", sa.Integer(), nullable=False, server_default="0"
        ),
    }
    for name, column in columns.items():
        if not _has_column("scan_cycles", name):
            op.add_column("scan_cycles", column)
    lease_columns = {
        "previous_worker_id": sa.Column(
            "previous_worker_id", sa.Text(), nullable=False, server_default=""
        ),
        "takeover_reason": sa.Column(
            "takeover_reason", sa.Text(), nullable=False, server_default=""
        ),
    }
    for name, column in lease_columns.items():
        if not _has_column("worker_leases", name):
            op.add_column("worker_leases", column)


def downgrade() -> None:
    for name in ("takeover_reason", "previous_worker_id"):
        if _has_column("worker_leases", name):
            op.drop_column("worker_leases", name)
    for name in (
        "partial_trace_count", "abandonment_reason", "abandoned_by_worker_id",
        "abandoned_at", "worker_id",
    ):
        if _has_column("scan_cycles", name):
            op.drop_column("scan_cycles", name)
