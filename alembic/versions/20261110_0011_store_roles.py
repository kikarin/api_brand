"""store roles + users.branch_id

Revision ID: 20261110_0011
Revises: 20261110_0010
Create Date: 2026-11-10

Roles baru (string enum): store_admin, fitting_attendant.
Attendant wajib punya branch_id (enforce di API create).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0011"
down_revision: Union[str, None] = "20261110_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_branch_id", "users", ["branch_id"])


def downgrade() -> None:
    op.drop_index("ix_users_branch_id", table_name="users")
    op.drop_column("users", "branch_id")
