from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SnapshotOut(BaseModel):
    id: UUID
    session_id: UUID
    brand_id: UUID
    mockup_id: UUID | None
    r2_key: str
    image_url: str
    content_path: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
