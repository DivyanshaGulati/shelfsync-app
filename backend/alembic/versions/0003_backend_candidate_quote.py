"""Persist deterministic backend candidate quotation for owner review.

Revision ID: 0003_backend_candidate_quote
Revises: 0002_quote_collection_deadline
"""
from alembic import op
import sqlalchemy as sa
revision = '0003_backend_candidate_quote'
down_revision = '0002_quote_collection_deadline'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('purchase_orders') as batch:
        batch.add_column(sa.Column('backend_candidate_quotation_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_po_backend_candidate_quote', 'vendor_quotations',
                                 ['backend_candidate_quotation_id'], ['id'], ondelete='SET NULL')

def downgrade():
    with op.batch_alter_table('purchase_orders') as batch:
        batch.drop_constraint('fk_po_backend_candidate_quote', type_='foreignkey')
        batch.drop_column('backend_candidate_quotation_id')
