"""add search status to audiobook

Revision ID: d1a9f3c72b8e
Revises: c4d8f2a1b5e3
Create Date: 2026-06-06 00:02:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1a9f3c72b8e"
down_revision: Union[str, None] = "c4d8f2a1b5e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "search_status",
                sa.String(),
                server_default="not_searched",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "search_note",
                sa.String(),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "last_searched_at",
                sa.DateTime(),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.drop_column("last_searched_at")
        batch_op.drop_column("search_note")
        batch_op.drop_column("search_status")
