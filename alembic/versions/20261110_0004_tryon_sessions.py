"""tryon_sessions

Revision ID: 20261110_0004
Revises: 20261110_0003
Create Date: 2026-11-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_0004"
down_revision: Union[str, None] = "20261110_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tryon_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("mockup_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("debited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("device_fingerprint", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mockup_id"], ["mockups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tryon_sessions_brand_id", "tryon_sessions", ["brand_id"])
    op.create_index("ix_tryon_sessions_user_id", "tryon_sessions", ["user_id"])
    op.create_index("ix_tryon_sessions_mockup_id", "tryon_sessions", ["mockup_id"])


def downgrade() -> None:
    op.drop_index("ix_tryon_sessions_mockup_id", table_name="tryon_sessions")
    op.drop_index("ix_tryon_sessions_user_id", table_name="tryon_sessions")
    op.drop_index("ix_tryon_sessions_brand_id", table_name="tryon_sessions")
    op.drop_table("tryon_sessions")
