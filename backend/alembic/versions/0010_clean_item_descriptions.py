"""Remove obsolete boilerplate from inventory descriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0010_clean_item_descriptions"
down_revision = "0009_purchase_tracking"
branch_labels = None
depends_on = None

BOILERPLATE = "Illustrative demo item; unit cost is not a current vendor quote."


def upgrade():
    items = sa.table("inventory_items", sa.column("description", sa.Text()))
    remaining = sa.func.trim(sa.func.replace(items.c.description, BOILERPLATE, ""))
    op.execute(
        items.update()
        .where(items.c.description.contains(BOILERPLATE))
        .values(description=sa.func.nullif(remaining, ""))
    )


def downgrade():
    # The schema is unchanged. There is no reliable way to recover the original text.
    pass
