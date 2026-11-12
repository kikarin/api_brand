"""fitting_visits room assign + tryon etalase_product_id

Revision ID: 20261111_0014
Revises: 20261111_0013
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261111_0014"
down_revision: Union[str, None] = "20261111_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fitting_visits",
        sa.Column("room_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "fitting_visits",
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "fitting_visits",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_fitting_visits_assigned_by_user_id",
        "fitting_visits",
        "users",
        ["assigned_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "tryon_sessions",
        sa.Column("etalase_product_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tryon_sessions_etalase_product_id",
        "tryon_sessions",
        "etalase_products",
        ["etalase_product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column("tryon_sessions", "mockup_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("tryon_sessions", "mockup_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_constraint("fk_tryon_sessions_etalase_product_id", "tryon_sessions", type_="foreignkey")
    op.drop_column("tryon_sessions", "etalase_product_id")
    op.drop_constraint("fk_fitting_visits_assigned_by_user_id", "fitting_visits", type_="foreignkey")
    op.drop_column("fitting_visits", "assigned_at")
    op.drop_column("fitting_visits", "assigned_by_user_id")
    op.drop_column("fitting_visits", "room_no")
