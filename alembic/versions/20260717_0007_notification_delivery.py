"""add explicit global Discord opt-in and notification attempt history

Revision ID: 20260717_0007
Revises: 20260716_0006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.db_models import notification_attempts


revision = "20260717_0007"
down_revision = "20260716_0006"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("user_notification_settings", "use_global_discord_webhook"):
        op.add_column(
            "user_notification_settings",
            sa.Column(
                "use_global_discord_webhook",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    notification_attempts.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    notification_attempts.drop(op.get_bind(), checkfirst=True)
    if _has_column("user_notification_settings", "use_global_discord_webhook"):
        op.drop_column("user_notification_settings", "use_global_discord_webhook")
