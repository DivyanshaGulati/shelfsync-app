"""Track orders and shipments produced by the mock purchasing workflow."""

from alembic import op
import sqlalchemy as sa


revision = "0008_mock_purchase_workflow"
down_revision = "0007_stock_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("mock_order", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("purchase_orders", sa.Column("mock_in_transit_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("purchase_orders", "mock_in_transit_at")
    op.drop_column("purchase_orders", "mock_order")
