"""Auditable idempotent physical stock receipts.

Revision ID: 0007_stock_receipts
Revises: 0006_purchase_order_email_dispatch
"""
from alembic import op
import sqlalchemy as sa

revision = '0007_stock_receipts'
down_revision = '0006_purchase_order_email_dispatch'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stock_receipts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('purchase_order_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('received_by', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(100), nullable=False, unique=True),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('quantity > 0', name='ck_stock_receipts_positive_quantity'),
        sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['received_by'], ['users.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_stock_receipts_purchase_order_id', 'stock_receipts', ['purchase_order_id'])


def downgrade():
    op.drop_index('ix_stock_receipts_purchase_order_id', table_name='stock_receipts')
    op.drop_table('stock_receipts')
