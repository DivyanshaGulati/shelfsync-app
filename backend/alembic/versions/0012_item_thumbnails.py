"""Store optional inventory item thumbnails."""

import sqlalchemy as sa
from alembic import op

revision = "0012_item_thumbnails"
down_revision = "0011_inventory_insight_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inventory_items", sa.Column("thumbnail", sa.String(12_000), nullable=True))


def downgrade():
    op.drop_column("inventory_items", "thumbnail")
