"""Separate advisory AI recommendation from backend purchasing candidate.

Revision ID: 0004_ai_recommendation
Revises: 0003_backend_candidate_quote
"""
from alembic import op
import sqlalchemy as sa
revision = '0004_ai_recommendation'
down_revision = '0003_backend_candidate_quote'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('purchase_orders') as batch:
        batch.add_column(sa.Column('ai_recommended_quotation_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('ai_recommendation_reason', sa.Text(), nullable=True))
        batch.create_foreign_key('fk_po_ai_recommended_quote', 'vendor_quotations',
                                 ['ai_recommended_quotation_id'], ['id'], ondelete='SET NULL')

def downgrade():
    with op.batch_alter_table('purchase_orders') as batch:
        batch.drop_constraint('fk_po_ai_recommended_quote', type_='foreignkey')
        batch.drop_column('ai_recommendation_reason')
        batch.drop_column('ai_recommended_quotation_id')
