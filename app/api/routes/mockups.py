import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from fastapi import Depends

from app.api.deps import (
    DbSession,
    RequireAnyMember,
    RequireDesignerPlus,
    require_brand_module,
)
from app.db.models import BrandModuleKind, Collection, Mockup, MockupCategory
from app.schemas.mockups import MockupOut, MockupUpdate
from app.services import prompt_templates
from app.services import r2 as r2_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/mockups",
    tags=["mockups"],
    dependencies=[Depends(require_brand_module(BrandModuleKind.studio))],
)


def _serialize(mockup: Mockup, *, warnings: list[str] | None = None) -> MockupOut:
    url = r2_service.resolve_image_url(mockup.image_r2_key) or mockup.image_url
    collection_name = mockup.collection.name if mockup.collection is not None else None
    return MockupOut(
        id=mockup.id,
        brand_id=mockup.brand_id,
        collection_id=mockup.collection_id,
        collection_name=collection_name,
        name=mockup.name,
        category=mockup.category,
        image_r2_key=mockup.image_r2_key,
        image_url=url,
        version_label=mockup.version_label,
        prompt_template_key=mockup.prompt_template_key,
        created_at=mockup.created_at,
        warnings=warnings or [],
    )


def _get_owned(db: DbSession, mockup_id: UUID, brand_id: UUID) -> Mockup:
    row = db.scalar(
        select(Mockup)
        .options(joinedload(Mockup.collection))
        .where(Mockup.id == mockup_id, Mockup.brand_id == brand_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mockup not found")
    return row


def _assert_collection(db: DbSession, collection_id: UUID | None, brand_id: UUID) -> None:
    if collection_id is None:
        return
    exists = db.scalar(
        select(Collection.id).where(
            Collection.id == collection_id,
            Collection.brand_id == brand_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid collection_id")


def _read_and_validate_image(file: UploadFile, data: bytes) -> tuple[str, str]:
    content_type = (file.content_type or "").lower()
    if content_type not in r2_service.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only jpeg, png, or webp allowed",
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(data) > r2_service.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large (max 10MB)",
        )
    return content_type, r2_service.ALLOWED_CONTENT_TYPES[content_type]


@router.get("/prompt-templates")
def get_prompt_templates(_: RequireAnyMember) -> list[dict[str, str]]:
    return prompt_templates.list_templates()


@router.get("", response_model=list[MockupOut])
def list_mockups(
    current: RequireAnyMember,
    db: DbSession,
    collection_id: UUID | None = None,
) -> list[MockupOut]:
    stmt = (
        select(Mockup)
        .options(joinedload(Mockup.collection))
        .where(Mockup.brand_id == current.brand_id)
    )
    if collection_id is not None:
        stmt = stmt.where(Mockup.collection_id == collection_id)
    rows = db.scalars(stmt.order_by(Mockup.created_at.desc())).unique().all()
    return [_serialize(m) for m in rows]


@router.get("/{mockup_id}", response_model=MockupOut)
def get_mockup(
    mockup_id: UUID,
    current: RequireAnyMember,
    db: DbSession,
) -> MockupOut:
    return _serialize(_get_owned(db, mockup_id, current.brand_id))


@router.post("", response_model=MockupOut, status_code=status.HTTP_201_CREATED)
async def create_mockup(
    current: RequireDesignerPlus,
    db: DbSession,
    name: str = Form(...),
    category: MockupCategory = Form(...),
    file: UploadFile = File(...),
    collection_id: UUID | None = Form(default=None),
    version_label: str | None = Form(default=None),
) -> MockupOut:
    if not r2_service.storage_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is not available on the server",
        )

    data = await file.read()
    content_type, ext = _read_and_validate_image(file, data)
    _assert_collection(db, collection_id, current.brand_id)

    mockup_id = uuid4()
    key = r2_service.build_object_key(
        brand_id=current.brand_id, mockup_id=mockup_id, ext=ext
    )

    try:
        r2_service.upload_bytes(key=key, data=data, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload to R2",
        ) from exc

    template_key = prompt_templates.template_key_for_category(category.value)
    image_url = r2_service.resolve_image_url(key)
    row = Mockup(
        id=mockup_id,
        brand_id=current.brand_id,
        collection_id=collection_id,
        name=name.strip(),
        category=category,
        image_r2_key=key,
        image_url=image_url,
        version_label=(version_label or "").strip() or None,
        prompt_template_key=template_key,
    )
    db.add(row)
    db.commit()
    row = _get_owned(db, mockup_id, current.brand_id)
    return _serialize(row)


@router.patch("/{mockup_id}", response_model=MockupOut)
def update_mockup(
    mockup_id: UUID,
    body: MockupUpdate,
    current: RequireDesignerPlus,
    db: DbSession,
) -> MockupOut:
    row = _get_owned(db, mockup_id, current.brand_id)

    if body.name is not None:
        row.name = body.name.strip()
    if body.category is not None:
        row.category = body.category
        row.prompt_template_key = prompt_templates.template_key_for_category(body.category.value)
    if body.version_label is not None:
        row.version_label = body.version_label.strip() or None
    if body.clear_collection:
        row.collection_id = None
    elif body.collection_id is not None:
        _assert_collection(db, body.collection_id, current.brand_id)
        row.collection_id = body.collection_id

    db.commit()
    row = _get_owned(db, mockup_id, current.brand_id)
    return _serialize(row)


@router.put("/{mockup_id}/image", response_model=MockupOut)
async def replace_mockup_image(
    mockup_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
    file: UploadFile = File(...),
) -> MockupOut:
    """Ganti file gambar mockup (upload baru → update key → hapus object lama best-effort)."""
    if not r2_service.storage_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is not available on the server",
        )

    row = _get_owned(db, mockup_id, current.brand_id)
    data = await file.read()
    content_type, ext = _read_and_validate_image(file, data)

    old_key = row.image_r2_key
    # Key baru unik supaya replace tidak overwrite object lama sebelum DB commit
    new_key = (
        f"brands/{current.brand_id}/mockups/{row.id}-{uuid4().hex[:10]}.{ext}"
    )

    try:
        r2_service.upload_bytes(key=new_key, data=data, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload to R2",
        ) from exc

    row.image_r2_key = new_key
    row.image_url = r2_service.resolve_image_url(new_key)
    db.commit()
    row = _get_owned(db, mockup_id, current.brand_id)

    if old_key and old_key != new_key:
        r2_service.delete_object(old_key)

    return _serialize(row)


@router.get("/{mockup_id}/file")
def download_mockup_file(
    mockup_id: UUID,
    current: RequireAnyMember,
    db: DbSession,
) -> Response:
    """Stream bytes via API — FE/Decart tidak fetch R2 langsung (CORS)."""
    row = _get_owned(db, mockup_id, current.brand_id)
    try:
        data, content_type = r2_service.get_object_bytes(row.image_r2_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("mockup_stream_failed id=%s", mockup_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal ambil mockup dari storage",
        ) from exc
    filename = row.image_r2_key.rsplit("/", 1)[-1] or f"{row.id}.png"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@router.delete("/{mockup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mockup(
    mockup_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> None:
    row = _get_owned(db, mockup_id, current.brand_id)
    key = row.image_r2_key
    db.delete(row)
    db.commit()
    r2_service.delete_object(key)
