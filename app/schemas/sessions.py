from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.db.models import SessionChannel, SessionStatus

EndReason = Literal["timeout", "abandoned", "error"]


class SessionStartRequest(BaseModel):
    """Select-then-try: `mockup_ids` (min 1). `mockup_id` legacy → diangkat ke list."""

    mockup_id: UUID | None = None
    mockup_ids: list[UUID] | None = None
    channel: SessionChannel = SessionChannel.studio
    device_fingerprint: str | None = Field(default=None, max_length=200)
    mobile_fallback: bool = False
    visit_id: UUID | None = None  # fitting_room (task-07)
    shopper_id: str | None = Field(default=None, max_length=200)  # ecommerce V2

    @model_validator(mode="after")
    def resolve_mockup_ids(self) -> "SessionStartRequest":
        ids = list(self.mockup_ids or [])
        if self.mockup_id is not None and self.mockup_id not in ids:
            ids.insert(0, self.mockup_id)
        # dedupe preserve order
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for mid in ids:
            if mid not in seen:
                seen.add(mid)
                unique.append(mid)
        if not unique:
            raise ValueError("mockup_ids (atau mockup_id) wajib, min 1")
        self.mockup_ids = unique
        self.mockup_id = unique[0]
        return self


class OutfitItemOut(BaseModel):
    mockup_id: UUID
    name: str
    category: str
    image_url: str
    prompt_template_key: str
    prompt: str


class SessionStartResponse(BaseModel):
    session_id: UUID
    status: SessionStatus
    tryon_mode: str
    client_token: str
    mockup_id: UUID
    mockup_ids: list[UUID]
    channel: SessionChannel
    prompt_template_key: str
    prompt: str
    image_url: str
    outfit: list[OutfitItemOut] = Field(default_factory=list)
    session_max_seconds: int
    remaining_ms: int | None = None
    ends_at: datetime | None = None  # legacy hint; prefer remaining_ms
    debited: bool = False
    person_detection_hard_gate: bool = False


class SessionEndRequest(BaseModel):
    reason: EndReason = "timeout"


class ClientTokenOut(BaseModel):
    """Short-lived Decart/mock client token — mint ulang untuk connect/reconnect."""

    client_token: str
    tryon_mode: str
    expires_hint_seconds: int | None = 300


class SessionGarmentRequest(BaseModel):
    mockup_id: UUID


class SessionOut(BaseModel):
    id: UUID
    brand_id: UUID
    mockup_id: UUID
    mockup_ids: list[UUID] | None = None
    channel: SessionChannel
    status: SessionStatus
    started_at: datetime | None
    ended_at: datetime | None
    remaining_ms: int | None = None
    remaining_updated_at: datetime | None = None
    duration_active_ms: int | None = None
    ends_at: datetime | None = None  # legacy derived hint
    duration_ms: int | None
    debited: bool
    error_code: str | None
    pause_count: int = 0
    resume_count: int = 0
    client_token: str | None = None
    session_max_seconds: int | None = None
    person_detection_hard_gate: bool | None = None
    outfit: list[OutfitItemOut] | None = None

    model_config = {"from_attributes": True}
