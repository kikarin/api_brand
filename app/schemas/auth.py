from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.db.models import BrandStatus, QuotaChannel, UserRole


class RegisterRequest(BaseModel):
    brand_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class BrandOut(BaseModel):
    id: UUID
    name: str
    slug: str
    status: BrandStatus
    plan: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: UserRole
    brand_id: UUID
    branch_id: UUID | None = None

    model_config = {"from_attributes": True}


class StoreUserCreate(BaseModel):
    """Invite/create akun toko — store_admin atau fitting_attendant."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole
    branch_id: UUID | None = None


class StoreUserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: UserRole
    brand_id: UUID
    branch_id: UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StudioUserCreate(BaseModel):
    """Owner invite designer/viewer ke brand yang sama."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole


class StudioUserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: UserRole
    brand_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    brand: BrandOut


class MeResponse(BaseModel):
    user: UserOut
    brand: BrandOut


class QuotaItem(BaseModel):
    channel: QuotaChannel
    balance: int
    period_start: datetime | None = None
    period_end: datetime | None = None


class QuotaResponse(BaseModel):
    brand_id: UUID
    wallets: list[QuotaItem]


class TryOnStatusOut(BaseModel):
    """Mode server + sisa credit Decart (live saja; mock = null)."""

    tryon_mode: str  # mock | live
    decart_credits: int | None = None
    decart_credits_error: str | None = None
