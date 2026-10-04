"""add cross-process worker leases

Revision ID: 20260711_0005
Revises: 20260707_0004
"""
from __future__ import annotations

from alembic import op

from backend.db_models import worker_leases

revision = "20260711_0005"
down_revision = "20260707_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    worker_leases.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    worker_leases.drop(op.get_bind(), checkfirst=True)
