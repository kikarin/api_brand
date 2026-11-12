from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from app.db.models import BrandModuleKind, BrandModuleStatus, BrandStatus, QuotaChannel


def _normalize_email(value: str) -> str:
    """Izinkan domain khusus lokal (mis. .local) yang ditolak EmailStr."""
    email = value.strip().lower()
    if "@" not in email:
        raise ValueError("email tidak valid")
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        raise ValueError("email tidak valid")
    return email


PlatformEmail = Annotated[str, AfterValidator(_normalize_email)]


class PlatformLoginRequest(BaseModel):
    email: PlatformEmail
    password: str = Field(min_length=8, max_length=128)


class PlatformAdminOut(BaseModel):
    id: UUID
    email: PlatformEmail
    created_at: datetime

    model_config = {"from_attributes": True}


class PlatformAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: PlatformAdminOut


class PlatformWalletOut(BaseModel):
    channel: QuotaChannel
    balance: int


class PlatformModuleOut(BaseModel):
    module: BrandModuleKind
    status: BrandModuleStatus


class PlatformBrandOut(BaseModel):
    id: UUID
    name: str
    slug: str
    status: BrandStatus
    plan: str
    created_at: datetime
    owner_email: str | None = None
    wallets: list[PlatformWalletOut] = Field(default_factory=list)
    modules: list[PlatformModuleOut] = Field(default_factory=list)


class PlatformBrandCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    owner_email: PlatformEmail
    owner_password: str = Field(min_length=8, max_length=128)
    studio_quota: int = Field(default=50, ge=0, le=1_000_000)
    fitting_quota: int = Field(default=10, ge=0, le=1_000_000)
    enable_fitting: bool = False


class PlatformBrandPatch(BaseModel):
    status: BrandStatus


class PlatformQuotaAdjust(BaseModel):
    channel: QuotaChannel
    delta: int = Field(ge=-1_000_000, le=1_000_000)
