"""brand_modules + etalase_products + branches.max_ai_rooms

Revision ID: 20261110_0010
Revises: 20261110_0009
Create Date: 2026-11-10

Default register: seed modul `studio` saja. Fitting Store = opt-in
(POST /api/brand/modules/fitting_store/enable).
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0010"
down_revision: Union[str, None] = "20261110_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brand_modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("brand_id", "module", name="uq_brand_modules_brand_module"),
    )
    op.create_index("ix_brand_modules_brand_id", "brand_modules", ["brand_id"])

    # Backfill: semua brand existing dapat modul studio aktif
    conn = op.get_bind()
    brand_ids = conn.execute(sa.text("SELECT id FROM brands")).fetchall()
    for (brand_id,) in brand_ids:
        conn.execute(
            sa.text(
                """
                INSERT INTO brand_modules (id, brand_id, module, status, created_at)
                SELECT :id, :brand_id, 'studio', 'active', now()
                WHERE NOT EXISTS (
                  SELECT 1 FROM brand_modules bm
                  WHERE bm.brand_id = :brand_id AND bm.module = 'studio'
                )
                """
            ),
            {"id": str(uuid4()), "brand_id": str(brand_id)},
        )

    op.create_table(
        "etalase_products",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sku", sa.String(80), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("image_r2_key", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_template_key", sa.String(40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_etalase_products_brand_id", "etalase_products", ["brand_id"])
    op.create_index("ix_etalase_products_branch_id", "etalase_products", ["branch_id"])

    # Rename capacity field → max_ai_rooms (data-model)
    op.alter_column(
        "branches",
        "max_concurrent_rooms",
        new_column_name="max_ai_rooms",
    )


def downgrade() -> None:
    op.alter_column(
        "branches",
        "max_ai_rooms",
        new_column_name="max_concurrent_rooms",
    )
    op.drop_index("ix_etalase_products_branch_id", table_name="etalase_products")
    op.drop_index("ix_etalase_products_brand_id", table_name="etalase_products")
    op.drop_table("etalase_products")
    op.drop_index("ix_brand_modules_brand_id", table_name="brand_modules")
    op.drop_table("brand_modules")
