"""Initial multi-user Notifierr schema.

Revision ID: 20260527_0001
Revises: None
Create Date: 2026-05-27 00:00:00
"""
from __future__ import annotations

from alembic import op

from backend.db_models import metadata


revision = "20260527_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    metadata.drop_all(bind)
