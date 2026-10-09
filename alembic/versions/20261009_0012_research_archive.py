"""Compact research evidence archive for hot/cold listing separation.

Revision ID: 20261009_0012
Revises: 20261004_0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20261009_0012"
down_revision = "20261004_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "research_evidence" not in tables:
        op.create_table(
            "research_evidence",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("marketplace_item_id", sa.Integer(), nullable=False),
            sa.Column("item_id", sa.Text(), nullable=False),
            sa.Column("evidence_day", sa.Text(), nullable=False),
            sa.Column("interesting", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("user_id", "item_id", name="uq_research_evidence_user_item"),
        )
        op.create_index("idx_research_evidence_day", "research_evidence", ["evidence_day", "id"])
        op.create_index("idx_research_evidence_user_day", "research_evidence", ["user_id", "evidence_day", "id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "research_evidence" in tables:
        op.drop_index("idx_research_evidence_user_day", table_name="research_evidence")
        op.drop_index("idx_research_evidence_day", table_name="research_evidence")
        op.drop_table("research_evidence")
