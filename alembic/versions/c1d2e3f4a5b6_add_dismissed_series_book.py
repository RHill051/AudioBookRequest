"""add dismissed series book

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-07-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dismissedseriesbook",
        sa.Column("asin", sa.String(), nullable=False),
        sa.Column("user_username", sa.String(), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asin"], ["audiobook.asin"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_username"], ["user.username"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("asin", "user_username"),
    )


def downgrade() -> None:
    op.drop_table("dismissedseriesbook")
