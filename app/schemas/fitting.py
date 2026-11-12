"""Fitting room — branches, visits, claim."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.sessions import OutfitItemOut, SessionStartResponse


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    max_ai_rooms: int = Field(default=2, ge=1, le=20)


class BranchOut(BaseModel):
    id: UUID
    brand_id: UUID
    name: str
    max_ai_rooms: int
    created_at: datetime

    model_config = {"from_attributes": True}


class VisitIssueRequest(BaseModel):
    mockup_ids: list[UUID] = Field(min_length=1, max_length=12)
    branch_id: UUID | None = None
    ttl_minutes: int = Field(default=30, ge=5, le=240)
    with_qr: bool = True


class VisitOut(BaseModel):
    id: UUID
    brand_id: UUID
    branch_id: UUID | None
    wishlist_code: str
    item_ids: list[UUID]
    expires_at: datetime
    claimed_at: datetime | None
    consumed_at: datetime | None
    session_id: UUID | None
    created_at: datetime
    room_no: int | None = None
    assigned_at: datetime | None = None
    claim_path: str | None = None
    qr_token: str | None = None
    qr_path: str | None = None
    qr_expires_at: datetime | None = None
    # open | assigned | claimed | in_session | consumed | expired
    status: str | None = None
    session_status: str | None = None

    model_config = {"from_attributes": True}


class PublicVisitOut(BaseModel):
    visit_id: UUID
    brand_name: str
    wishlist_code: str
    status: str  # open | assigned | claimed | in_session | consumed | expired
    expires_at: datetime
    claimed_at: datetime | None
    consumed_at: datetime | None
    room_no: int | None = None
    assigned_at: datetime | None = None
    outfit: list[OutfitItemOut] = Field(default_factory=list)
    session_max_seconds: int = 300
    # Agar FE bisa Lanjut setelah refresh/remount (bukan start sesi kedua)
    session_id: UUID | None = None
    session_status: str | None = None
    remaining_ms: int | None = None
    remaining_updated_at: datetime | None = None
    mockup_id: UUID | None = None  # active garment id (etalase product id for fitting)


class PublicWishlistCreate(BaseModel):
    product_ids: list[UUID] = Field(min_length=1)
    branch_id: UUID | None = None
    ttl_minutes: int = Field(default=30, ge=5, le=240)


class VisitAssignRequest(BaseModel):
    room_no: int = Field(ge=1, le=20)


class FittingStartRequest(BaseModel):
    device_fingerprint: str | None = Field(default=None, max_length=200)
    mockup_id: UUID | None = None  # active etalase product id override (must be in queue)
    room_no: int | None = Field(default=None, ge=1, le=20)



class FittingStartResponse(SessionStartResponse):
    visit_id: UUID
    wishlist_code: str
