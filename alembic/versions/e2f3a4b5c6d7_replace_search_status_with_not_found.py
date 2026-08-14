"""replace search status with not found

Revision ID: e2f3a4b5c6d7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "not_found",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "not_found_at",
                sa.DateTime(),
                nullable=True,
            )
        )

    audiobook = sa.table(
        "audiobook",
        sa.column("search_status", sa.String()),
        sa.column("last_searched_at", sa.DateTime()),
        sa.column("not_found", sa.Boolean()),
        sa.column("not_found_at", sa.DateTime()),
    )
    op.execute(
        audiobook.update()
        .where(audiobook.c.search_status == "not_found")
        .values(not_found=True, not_found_at=audiobook.c.last_searched_at)
    )

    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.drop_column("last_searched_at")
        batch_op.drop_column("search_note")
        batch_op.drop_column("search_status")


def downgrade() -> None:
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

    audiobook = sa.table(
        "audiobook",
        sa.column("not_found", sa.Boolean()),
        sa.column("not_found_at", sa.DateTime()),
        sa.column("search_status", sa.String()),
        sa.column("last_searched_at", sa.DateTime()),
    )
    op.execute(
        audiobook.update()
        .where(audiobook.c.not_found.is_(True))
        .values(search_status="not_found", last_searched_at=audiobook.c.not_found_at)
    )

    with op.batch_alter_table("audiobook", schema=None) as batch_op:
        batch_op.drop_column("not_found_at")
        batch_op.drop_column("not_found")
