"""session pause counters

Revision ID: 20261110_0005
Revises: 20261110_0004
Create Date: 2026-11-10

Status `paused` memakai kolom string existing — tidak perlu enum DB.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0005"
down_revision: Union[str, None] = "20261110_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tryon_sessions",
        sa.Column("pause_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tryon_sessions",
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("tryon_sessions", "resume_count")
    op.drop_column("tryon_sessions", "pause_count")
