"""Add scan instrumentation tables.

Revision ID: 20260706_0002
Revises: 20260527_0001
Create Date: 2026-07-06 00:00:00
"""
from __future__ import annotations

from alembic import op

from backend.db_models import listing_decision_traces, scan_cycles, worker_heartbeats


revision = "20260706_0002"
down_revision = "20260527_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    scan_cycles.create(bind, checkfirst=True)
    worker_heartbeats.create(bind, checkfirst=True)
    listing_decision_traces.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    listing_decision_traces.drop(bind, checkfirst=True)
    worker_heartbeats.drop(bind, checkfirst=True)
    scan_cycles.drop(bind, checkfirst=True)
