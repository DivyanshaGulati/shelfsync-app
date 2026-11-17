"""Persist simulated delivery tracking for standard purchase orders."""

import sqlalchemy as sa
from alembic import op


revision = "0009_purchase_tracking"
down_revision = "0008_mock_purchase_workflow"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("mock_delivered_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("purchase_orders", "mock_delivered_at")
