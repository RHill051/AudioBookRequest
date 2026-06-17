"""add series fields to audiobook

Revision ID: a1b2c3d4e5f6
Revises: 506b89151b6f
Create Date: 2026-06-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "506b89151b6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.add_column(sa.Column("series_asin", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("series_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("series_number", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.drop_column("series_number")
        batch_op.drop_column("series_name")
        batch_op.drop_column("series_asin")
