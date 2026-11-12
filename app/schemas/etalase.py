"""Etalase products — Fitting Store katalog (≠ mockups)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import MockupCategory


class EtalaseProductOut(BaseModel):
    id: UUID
    brand_id: UUID
    branch_id: UUID | None
    name: str
    sku: str | None
    category: MockupCategory
    image_r2_key: str
    image_url: str
    prompt_template_key: str
    is_active: bool
    created_at: datetime
    warnings: list[str] = Field(default_factory=list)
    # QR permanen (task-12)
    qr_token: str | None = None
    qr_path: str | None = None  # /e/{token}

    model_config = {"from_attributes": True}


class PublicEtalaseProductOut(BaseModel):
    """Resolve scan QR etalase — tanpa auth."""

    product_id: UUID
    brand_id: UUID
    brand_name: str
    brand_slug: str | None = None
    name: str
    sku: str | None
    category: str
    image_url: str
    is_active: bool
    qr_token: str | None = None
    qr_path: str | None = None
    wishlist_hint: str = (
        "Tambah ke wishlist, konfirmasi, lalu serahkan kode ke attendant."
    )


class PublicEtalaseListItem(BaseModel):
    product_id: UUID
    name: str
    sku: str | None
    category: str
    image_url: str
    qr_token: str | None = None
    qr_path: str | None = None


class EtalaseProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = None
    clear_sku: bool = False
    category: MockupCategory | None = None
    branch_id: UUID | None = None
    clear_branch: bool = False
    is_active: bool | None = None


class BrandModuleOut(BaseModel):
    id: UUID
    brand_id: UUID
    module: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BranchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    max_ai_rooms: int | None = Field(default=None, ge=1, le=20)
