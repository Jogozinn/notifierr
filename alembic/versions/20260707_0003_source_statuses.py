"""add source status cooldown tracking"""

from __future__ import annotations

from alembic import op

from backend.db_models import source_statuses


revision = "20260707_0003"
down_revision = "20260706_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    source_statuses.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    source_statuses.drop(bind, checkfirst=True)
