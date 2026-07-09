"""add scan cycle source cooldown metadata"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260707_0004"
down_revision = "20260707_0003"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = [
        sa.Column("source", sa.Text()),
        sa.Column("error_category", sa.Text()),
        sa.Column("http_status", sa.Integer()),
        sa.Column("cooldown_until", sa.Text()),
        sa.Column("retry_after_seconds", sa.Integer()),
    ]
    for column in columns:
        if not _has_column("scan_cycles", column.name):
            op.add_column("scan_cycles", column)


def downgrade() -> None:
    for column_name in (
        "retry_after_seconds",
        "cooldown_until",
        "http_status",
        "error_category",
        "source",
    ):
        if _has_column("scan_cycles", column_name):
            op.drop_column("scan_cycles", column_name)
