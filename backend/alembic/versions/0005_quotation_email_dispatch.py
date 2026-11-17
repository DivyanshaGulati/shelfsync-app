"""Persistent safe RFQ email dispatch reservations.

Revision ID: 0005_quotation_email_dispatch
Revises: 0004_ai_recommendation
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_quotation_email_dispatch'
down_revision = '0004_ai_recommendation'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'quotation_email_dispatches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('quotation_id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('recipient', sa.String(254), nullable=False),
        sa.Column('message_id', sa.String(254), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('error_code', sa.String(60)),
        sa.CheckConstraint("status IN ('IN_PROGRESS', 'MOCKED', 'SENT', 'UNCERTAIN')", name='ck_quotation_email_dispatch_status'),
        sa.ForeignKeyConstraint(['quotation_id'], ['vendor_quotations.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_quotation_email_dispatches_quotation_id', 'quotation_email_dispatches', ['quotation_id'], unique=True)

def downgrade():
    op.drop_index('ix_quotation_email_dispatches_quotation_id', table_name='quotation_email_dispatches')
    op.drop_table('quotation_email_dispatches')
