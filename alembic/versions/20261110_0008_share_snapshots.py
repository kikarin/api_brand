"""share_links + snapshots + tryon_sessions.share_link_id

Revision ID: 20261110_0008
Revises: 20261110_0007
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0008"
down_revision: Union[str, None] = "20261110_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column(
            "mockup_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_sessions", sa.Integer(), nullable=True),
        sa.Column("sessions_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_share_links_brand_id", "share_links", ["brand_id"])
    op.create_index("ix_share_links_token", "share_links", ["token"], unique=True)

    op.add_column(
        "tryon_sessions",
        sa.Column(
            "share_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("share_links.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_tryon_sessions_share_link_id", "tryon_sessions", ["share_link_id"])

    op.create_table(
        "snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tryon_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mockup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mockups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("r2_key", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_snapshots_session_id", "snapshots", ["session_id"])
    op.create_index("ix_snapshots_brand_id", "snapshots", ["brand_id"])
    op.create_index("ix_snapshots_mockup_id", "snapshots", ["mockup_id"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_mockup_id", table_name="snapshots")
    op.drop_index("ix_snapshots_brand_id", table_name="snapshots")
    op.drop_index("ix_snapshots_session_id", table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_index("ix_tryon_sessions_share_link_id", table_name="tryon_sessions")
    op.drop_column("tryon_sessions", "share_link_id")
    op.drop_index("ix_share_links_token", table_name="share_links")
    op.drop_index("ix_share_links_brand_id", table_name="share_links")
    op.drop_table("share_links")
