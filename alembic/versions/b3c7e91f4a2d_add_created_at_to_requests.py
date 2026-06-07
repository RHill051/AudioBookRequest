"""add created_at to request tables

Revision ID: b3c7e91f4a2d
Revises: 1718055d5ca8
Create Date: 2026-06-06 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3c7e91f4a2d"
down_revision: Union[str, None] = "1718055d5ca8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audiobookrequest", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )

    with op.batch_alter_table("manualbookrequest", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("audiobookrequest", schema=None) as batch_op:
        batch_op.drop_column("created_at")

    with op.batch_alter_table("manualbookrequest", schema=None) as batch_op:
        batch_op.drop_column("created_at")
