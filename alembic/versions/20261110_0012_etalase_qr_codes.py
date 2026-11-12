"""etalase_qr_codes — QR permanen per produk etalase

Revision ID: 20261110_0012
Revises: 20261110_0011
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0012"
down_revision: Union[str, None] = "20261110_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "etalase_qr_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("etalase_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_etalase_qr_codes_brand_id", "etalase_qr_codes", ["brand_id"])
    op.create_index("ix_etalase_qr_codes_product_id", "etalase_qr_codes", ["product_id"])
    op.create_index("ix_etalase_qr_codes_token", "etalase_qr_codes", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_etalase_qr_codes_token", table_name="etalase_qr_codes")
    op.drop_index("ix_etalase_qr_codes_product_id", table_name="etalase_qr_codes")
    op.drop_index("ix_etalase_qr_codes_brand_id", table_name="etalase_qr_codes")
    op.drop_table("etalase_qr_codes")
