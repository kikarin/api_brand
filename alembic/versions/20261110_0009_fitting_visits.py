"""branches + fitting_visits + qr_tokens + tryon_sessions.visit_id

Revision ID: 20261110_0009
Revises: 20261110_0008
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0009"
down_revision: Union[str, None] = "20261110_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("max_concurrent_rooms", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_branches_brand_id", "branches", ["brand_id"])

    op.create_table(
        "fitting_visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("wishlist_code", sa.String(16), nullable=False),
        sa.Column(
            "item_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "issued_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("wishlist_code", name="uq_fitting_visits_wishlist_code"),
    )
    op.create_index("ix_fitting_visits_brand_id", "fitting_visits", ["brand_id"])
    op.create_index("ix_fitting_visits_branch_id", "fitting_visits", ["branch_id"])
    op.create_index("ix_fitting_visits_wishlist_code", "fitting_visits", ["wishlist_code"])
    op.create_index("ix_fitting_visits_session_id", "fitting_visits", ["session_id"])

    op.add_column(
        "tryon_sessions",
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_tryon_sessions_visit_id", "tryon_sessions", ["visit_id"])
    op.create_foreign_key(
        "fk_tryon_sessions_visit_id",
        "tryon_sessions",
        "fitting_visits",
        ["visit_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_fitting_visits_session_id",
        "fitting_visits",
        "tryon_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "qr_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fitting_visits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "claimed_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tryon_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("token", name="uq_qr_tokens_token"),
    )
    op.create_index("ix_qr_tokens_brand_id", "qr_tokens", ["brand_id"])
    op.create_index("ix_qr_tokens_visit_id", "qr_tokens", ["visit_id"])
    op.create_index("ix_qr_tokens_token", "qr_tokens", ["token"])


def downgrade() -> None:
    op.drop_table("qr_tokens")
    op.drop_constraint("fk_fitting_visits_session_id", "fitting_visits", type_="foreignkey")
    op.drop_constraint("fk_tryon_sessions_visit_id", "tryon_sessions", type_="foreignkey")
    op.drop_index("ix_tryon_sessions_visit_id", table_name="tryon_sessions")
    op.drop_column("tryon_sessions", "visit_id")
    op.drop_table("fitting_visits")
    op.drop_table("branches")
