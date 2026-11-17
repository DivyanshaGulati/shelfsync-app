"""Create ShelfSync schema (10 core tables).

Revision ID: 0001_initial_schema
Revises:
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table('inventory_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('on_hand', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('reserved_qty', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('unit', sa.String(length=40), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=2), server_default=sa.text('0'), nullable=False),
    sa.Column('avg_daily_demand', sa.Numeric(precision=14, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('demand_std_dev', sa.Numeric(precision=14, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('avg_lead_time_days', sa.Numeric(precision=10, scale=3), server_default=sa.text('0'), nullable=False),
    sa.Column('lead_time_std_dev', sa.Numeric(precision=10, scale=3), server_default=sa.text('0'), nullable=False),
    sa.Column('safety_stock', sa.Numeric(precision=14, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('reorder_point', sa.Numeric(precision=14, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('low_confidence', sa.Boolean(), server_default=sa.text('(true)'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('avg_daily_demand >= 0 AND demand_std_dev >= 0 AND avg_lead_time_days >= 0 AND lead_time_std_dev >= 0 AND safety_stock >= 0 AND reorder_point >= 0', name='ck_inventory_forecast_nonnegative'),
    sa.CheckConstraint('on_hand >= 0 AND reserved_qty >= 0 AND reserved_qty <= on_hand', name='ck_inventory_quantities'),
    sa.CheckConstraint('unit_cost >= 0', name='ck_inventory_unit_cost'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_inventory_items_category'), 'inventory_items', ['category'], unique=False)
    op.create_index(op.f('ix_inventory_items_sku'), 'inventory_items', ['sku'], unique=True)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('(true)'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("role IN ('OWNER', 'EMPLOYEE')", name='ck_users_role'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('vendors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('(true)'), nullable=False),
    sa.Column('is_new_vendor', sa.Boolean(), server_default=sa.text('(true)'), nullable=False),
    sa.Column('is_slow', sa.Boolean(), server_default=sa.text('(false)'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('ai_threshold_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('avg_daily_demand', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('demand_std_dev', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('avg_lead_time_days', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('lead_time_std_dev', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('safety_stock', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('reorder_point', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('inventory_position', sa.Integer(), nullable=False),
    sa.Column('calculation_reason', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_threshold_logs_inventory_item_id'), 'ai_threshold_logs', ['inventory_item_id'], unique=False)
    op.create_table('purchase_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=30), server_default=sa.text("'WAITING_FOR_QUOTES'"), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('approval_reason', sa.Text(), nullable=True),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("status != 'WAITING_FOR_QUOTES' OR (vendor_id IS NULL AND total_amount IS NULL)", name='ck_purchase_orders_waiting_no_price'),
    sa.CheckConstraint("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'REJECTED')", name='ck_purchase_orders_status'),
    sa.CheckConstraint('total_amount IS NULL OR total_amount >= 0', name='ck_purchase_orders_total'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'inventory_item_id', name='uq_purchase_orders_id_item')
    )
    op.create_index(op.f('ix_purchase_orders_inventory_item_id'), 'purchase_orders', ['inventory_item_id'], unique=False)
    op.create_index('ix_purchase_orders_one_open_per_item', 'purchase_orders', ['inventory_item_id'], unique=True, postgresql_where=sa.text("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED')"), sqlite_where=sa.text("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED')"))
    op.create_index(op.f('ix_purchase_orders_status'), 'purchase_orders', ['status'], unique=False)
    op.create_table('transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('transaction_type', sa.String(length=20), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('usage_note', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("transaction_type IN ('CHECKOUT', 'ADJUSTMENT', 'RECEIPT')", name='ck_transactions_type'),
    sa.CheckConstraint('quantity > 0', name='ck_transactions_positive_qty'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_transactions_created_at', 'transactions', ['created_at'], unique=False)
    op.create_index(op.f('ix_transactions_inventory_item_id'), 'transactions', ['inventory_item_id'], unique=False)
    op.create_table('vendor_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=False),
    sa.Column('is_preferred', sa.Boolean(), server_default=sa.text('(false)'), nullable=False),
    sa.Column('last_known_unit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('expected_lead_time_days', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('min_order_qty', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('expected_lead_time_days IS NULL OR expected_lead_time_days >= 0', name='ck_vendor_items_lead_time'),
    sa.CheckConstraint('last_known_unit_price IS NULL OR last_known_unit_price >= 0', name='ck_vendor_items_price'),
    sa.CheckConstraint('min_order_qty > 0', name='ck_vendor_items_min_qty'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('inventory_item_id', 'vendor_id', name='uq_vendor_items_item_vendor')
    )
    op.create_index(op.f('ix_vendor_items_inventory_item_id'), 'vendor_items', ['inventory_item_id'], unique=False)
    op.create_index('ix_vendor_items_one_preferred', 'vendor_items', ['inventory_item_id'], unique=True, postgresql_where=sa.text('is_preferred = true'), sqlite_where=sa.text('is_preferred = 1'))
    op.create_index(op.f('ix_vendor_items_vendor_id'), 'vendor_items', ['vendor_id'], unique=False)
    op.create_table('approval_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('purchase_order_id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('comments', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("action IN ('APPROVED', 'REJECTED')", name='ck_approval_logs_action'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approval_logs_purchase_order_id'), 'approval_logs', ['purchase_order_id'], unique=False)
    op.create_table('order_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('purchase_order_id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('ordered_quantity', sa.Integer(), nullable=False),
    sa.Column('received_quantity', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('total_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('is_open', sa.Boolean(), server_default=sa.text('(true)'), nullable=False),
    sa.CheckConstraint('ordered_quantity > 0 AND received_quantity >= 0 AND received_quantity <= ordered_quantity', name='ck_order_items_qty'),
    sa.CheckConstraint('total_price IS NULL OR total_price >= 0', name='ck_order_items_total_price'),
    sa.CheckConstraint('unit_price IS NULL OR unit_price >= 0', name='ck_order_items_unit_price'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_id', 'inventory_item_id'], ['purchase_orders.id', 'purchase_orders.inventory_item_id'], name='fk_order_items_matching_order', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('purchase_order_id', 'inventory_item_id', name='uq_order_items_order_item')
    )
    op.create_index(op.f('ix_order_items_inventory_item_id'), 'order_items', ['inventory_item_id'], unique=False)
    op.create_index(op.f('ix_order_items_purchase_order_id'), 'order_items', ['purchase_order_id'], unique=False)
    op.create_table('vendor_quotations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('purchase_order_id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=False),
    sa.Column('quoted_unit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('quoted_lead_time_days', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'REQUESTED'"), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_simulated', sa.Boolean(), server_default=sa.text('(false)'), nullable=False),
    sa.CheckConstraint("status IN ('REQUESTED', 'RECEIVED', 'SELECTED', 'EXPIRED')", name='ck_vendor_quotations_status'),
    sa.CheckConstraint("status NOT IN ('RECEIVED', 'SELECTED') OR (quoted_unit_price IS NOT NULL AND quoted_lead_time_days IS NOT NULL AND responded_at IS NOT NULL)", name='ck_vendor_quotations_received_data'),
    sa.CheckConstraint('quoted_lead_time_days IS NULL OR quoted_lead_time_days >= 0', name='ck_vendor_quotations_lead_time'),
    sa.CheckConstraint('quoted_unit_price IS NULL OR quoted_unit_price >= 0', name='ck_vendor_quotations_price'),
    sa.ForeignKeyConstraint(['inventory_item_id', 'vendor_id'], ['vendor_items.inventory_item_id', 'vendor_items.vendor_id'], name='fk_quotations_mapped_vendor', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_id', 'inventory_item_id'], ['purchase_orders.id', 'purchase_orders.inventory_item_id'], name='fk_quotations_matching_order', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('purchase_order_id', 'vendor_id', name='uq_vendor_quotations_order_vendor')
    )
    op.create_index(op.f('ix_vendor_quotations_inventory_item_id'), 'vendor_quotations', ['inventory_item_id'], unique=False)
    op.create_index('ix_vendor_quotations_one_selected', 'vendor_quotations', ['purchase_order_id'], unique=True, postgresql_where=sa.text("status = 'SELECTED'"), sqlite_where=sa.text("status = 'SELECTED'"))
    op.create_index(op.f('ix_vendor_quotations_purchase_order_id'), 'vendor_quotations', ['purchase_order_id'], unique=False)
    op.create_index(op.f('ix_vendor_quotations_status'), 'vendor_quotations', ['status'], unique=False)
    op.create_index(op.f('ix_vendor_quotations_vendor_id'), 'vendor_quotations', ['vendor_id'], unique=False)


def downgrade() -> None:

    op.drop_index(op.f('ix_vendor_quotations_vendor_id'), table_name='vendor_quotations')
    op.drop_index(op.f('ix_vendor_quotations_status'), table_name='vendor_quotations')
    op.drop_index(op.f('ix_vendor_quotations_purchase_order_id'), table_name='vendor_quotations')
    op.drop_index('ix_vendor_quotations_one_selected', table_name='vendor_quotations', postgresql_where=sa.text("status = 'SELECTED'"), sqlite_where=sa.text("status = 'SELECTED'"))
    op.drop_index(op.f('ix_vendor_quotations_inventory_item_id'), table_name='vendor_quotations')
    op.drop_table('vendor_quotations')
    op.drop_index(op.f('ix_order_items_purchase_order_id'), table_name='order_items')
    op.drop_index(op.f('ix_order_items_inventory_item_id'), table_name='order_items')
    op.drop_table('order_items')
    op.drop_index(op.f('ix_approval_logs_purchase_order_id'), table_name='approval_logs')
    op.drop_table('approval_logs')
    op.drop_index(op.f('ix_vendor_items_vendor_id'), table_name='vendor_items')
    op.drop_index('ix_vendor_items_one_preferred', table_name='vendor_items', postgresql_where=sa.text('is_preferred = true'), sqlite_where=sa.text('is_preferred = 1'))
    op.drop_index(op.f('ix_vendor_items_inventory_item_id'), table_name='vendor_items')
    op.drop_table('vendor_items')
    op.drop_index(op.f('ix_transactions_inventory_item_id'), table_name='transactions')
    op.drop_index('ix_transactions_created_at', table_name='transactions')
    op.drop_table('transactions')
    op.drop_index(op.f('ix_purchase_orders_status'), table_name='purchase_orders')
    op.drop_index('ix_purchase_orders_one_open_per_item', table_name='purchase_orders', postgresql_where=sa.text("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED')"), sqlite_where=sa.text("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED')"))
    op.drop_index(op.f('ix_purchase_orders_inventory_item_id'), table_name='purchase_orders')
    op.drop_table('purchase_orders')
    op.drop_index(op.f('ix_ai_threshold_logs_inventory_item_id'), table_name='ai_threshold_logs')
    op.drop_table('ai_threshold_logs')
    op.drop_table('vendors')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_inventory_items_sku'), table_name='inventory_items')
    op.drop_index(op.f('ix_inventory_items_category'), table_name='inventory_items')
    op.drop_table('inventory_items')
