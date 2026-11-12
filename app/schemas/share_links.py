from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ShareLinkCreate(BaseModel):
    mockup_ids: list[UUID] = Field(min_length=1, max_length=2)
    expires_at: datetime
    password: str | None = Field(default=None, max_length=128)
    max_sessions: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("mockup_ids")
    @classmethod
    def dedupe_ids(cls, v: list[UUID]) -> list[UUID]:
        seen: set[UUID] = set()
        out: list[UUID] = []
        for mid in v:
            if mid not in seen:
                seen.add(mid)
                out.append(mid)
        if not out:
            raise ValueError("mockup_ids wajib")
        return out


class ShareMockupPublic(BaseModel):
    id: UUID
    name: str
    category: str
    image_url: str
    version_label: str | None = None
    prompt_template_key: str


class ShareLinkOut(BaseModel):
    id: UUID
    brand_id: UUID
    token: str
    url_path: str
    mockup_ids: list[UUID]
    has_password: bool
    expires_at: datetime
    max_sessions: int | None
    sessions_used: int
    created_at: datetime
    mockups: list[ShareMockupPublic] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class SharePublicOut(BaseModel):
    token: str
    brand_name: str
    requires_password: bool
    expires_at: datetime
    max_sessions: int | None
    sessions_used: int
    remaining_sessions: int | None
    unlocked: bool
    mockups: list[ShareMockupPublic] = Field(default_factory=list)
    tryon_mode: str = "mock"
    # JWT setelah unlock — FE simpan sessionStorage, kirim ulang saat reload
    access_token: str | None = None


class ShareUnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ShareStartRequest(BaseModel):
    password: str | None = None
    access_token: str | None = None
    device_fingerprint: str | None = Field(default=None, max_length=200)
