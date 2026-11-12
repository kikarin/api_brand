from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import MockupCategory


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CollectionUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CollectionOut(BaseModel):
    id: UUID
    brand_id: UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MockupOut(BaseModel):
    id: UUID
    brand_id: UUID
    collection_id: UUID | None
    collection_name: str | None = None
    name: str
    category: MockupCategory
    image_r2_key: str
    image_url: str
    version_label: str | None
    prompt_template_key: str
    created_at: datetime
    warnings: list[str] = []

    model_config = {"from_attributes": True}


class MockupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: MockupCategory | None = None
    collection_id: UUID | None = None
    version_label: str | None = Field(default=None, max_length=40)
    clear_collection: bool = False
