"""tryon_sessions.ends_at + channel model (string values)

Revision ID: 20261110_0006
Revises: 20261110_0005
Create Date: 2026-11-10

Cooldown cooldown global dihapus di aplikasi; kolom ends_at menyimpan
deadline wall-clock per channel sejak pertama active.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0006"
down_revision: Union[str, None] = "20261110_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tryon_sessions",
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tryon_sessions", "ends_at")
