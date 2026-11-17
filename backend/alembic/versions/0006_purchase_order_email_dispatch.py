"""Durable final purchase-order email dispatch reservations.

Revision ID: 0006_purchase_order_email_dispatch
Revises: 0005_quotation_email_dispatch
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_purchase_order_email_dispatch'
down_revision = '0005_quotation_email_dispatch'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == 'postgresql':
        # The existing revision ID exceeds Alembic's default VARCHAR(32).
        op.alter_column('alembic_version', 'version_num', type_=sa.String(64))
    op.create_table(
        'purchase_order_email_dispatches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('purchase_order_id', sa.Integer(), nullable=False),
        sa.Column('quotation_id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('recipient', sa.String(254), nullable=False),
        sa.Column('message_id', sa.String(254), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('error_code', sa.String(60)),
        sa.CheckConstraint("status IN ('IN_PROGRESS', 'MOCKED', 'SENT', 'UNCERTAIN')",
                           name='ck_purchase_order_email_dispatch_status'),
        sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['quotation_id'], ['vendor_quotations.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_purchase_order_email_dispatches_purchase_order_id',
                    'purchase_order_email_dispatches', ['purchase_order_id'], unique=True)


def downgrade():
    op.drop_index('ix_purchase_order_email_dispatches_purchase_order_id',
                  table_name='purchase_order_email_dispatches')
    op.drop_table('purchase_order_email_dispatches')
