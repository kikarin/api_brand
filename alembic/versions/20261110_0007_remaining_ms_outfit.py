"""tryon_sessions remaining_ms + mockup_ids (revisi 10b)

Revision ID: 20261110_0007
Revises: 20261110_0006
Create Date: 2026-11-10

Timer budget aktif = remaining_ms (beku saat paused).
Outfit studio = mockup_ids[]. Legacy ends_at bukan sumber kebenaran.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0007"
down_revision: Union[str, None] = "20261110_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tryon_sessions",
        sa.Column("remaining_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tryon_sessions",
        sa.Column("remaining_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tryon_sessions",
        sa.Column("duration_active_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tryon_sessions",
        sa.Column(
            "mockup_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )
    # Backfill: outfit = [mockup_id]
    op.execute(
        """
        UPDATE tryon_sessions
        SET mockup_ids = ARRAY[mockup_id]
        WHERE mockup_ids IS NULL AND mockup_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("tryon_sessions", "mockup_ids")
    op.drop_column("tryon_sessions", "duration_active_ms")
    op.drop_column("tryon_sessions", "remaining_updated_at")
    op.drop_column("tryon_sessions", "remaining_ms")
