"""Save inventory insight reports."""

import sqlalchemy as sa
from alembic import op

revision = "0011_inventory_insight_reports"
down_revision = "0010_clean_item_descriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_insight_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_inventory_insight_reports_owner_id_id", "inventory_insight_reports", ["owner_id", "id"])


def downgrade():
    op.drop_table("inventory_insight_reports")
