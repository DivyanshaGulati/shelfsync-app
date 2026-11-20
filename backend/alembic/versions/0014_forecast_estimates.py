"""Store optional starting estimates for inventory forecasts."""

import sqlalchemy as sa
from alembic import op

revision = "0014_forecast_estimates"
down_revision = "0013_vendor_bills"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inventory_items", sa.Column("estimated_daily_demand", sa.Numeric(10, 4)))
    op.add_column("inventory_items", sa.Column("estimated_lead_time_days", sa.Numeric(6, 3)))


def downgrade():
    op.drop_column("inventory_items", "estimated_lead_time_days")
    op.drop_column("inventory_items", "estimated_daily_demand")
