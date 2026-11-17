"""Snapshot quote collection deadline per replenishment request.

Revision ID: 0002_quote_collection_deadline
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_quote_collection_deadline"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("quote_deadline_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("purchase_orders", "quote_deadline_at")
