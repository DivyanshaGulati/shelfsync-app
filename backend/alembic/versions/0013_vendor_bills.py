"""Store one simulated vendor bill per delivered purchase order."""

import sqlalchemy as sa
from alembic import op

revision = "0013_vendor_bills"
down_revision = "0012_item_thumbnails"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vendor_bills",
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("check_status", sa.String(16), nullable=False),
    )


def downgrade():
    op.drop_table("vendor_bills")
