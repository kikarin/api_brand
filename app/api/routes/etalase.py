"""Etalase products CRUD + QR permanen (Fitting Store ≠ mockups).

Auth: owner / store_admin + modul fitting_store.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import DbSession, RequireEtalaseAdmin
from app.db.models import Brand, Branch, EtalaseProduct, MockupCategory
from app.schemas.etalase import (
    EtalaseProductOut,
    EtalaseProductUpdate,
    PublicEtalaseProductOut,
)
from app.services import etalase_qr as qr_svc
from app.services import prompt_templates
from app.services import r2 as r2_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["etalase"])


def _qr_fields(token: str | None) -> dict[str, str | None]:
    if not token:
        return {"qr_token": None, "qr_path": None}
    return {"qr_token": token, "qr_path": f"/e/{token}"}


def _serialize(
    row: EtalaseProduct,
    *,
    qr_token: str | None = None,
    warnings: list[str] | None = None,
) -> EtalaseProductOut:
    url = r2_service.resolve_image_url(row.image_r2_key) or row.image_url
    return EtalaseProductOut(
        id=row.id,
        brand_id=row.brand_id,
        branch_id=row.branch_id,
        name=row.name,
        sku=row.sku,
        category=row.category,
        image_r2_key=row.image_r2_key,
        image_url=url,
        prompt_template_key=row.prompt_template_key,
        is_active=row.is_active,
        created_at=row.created_at,
        warnings=warnings or [],
        **_qr_fields(qr_token),
    )


def _serialize_with_qr(db: DbSession, row: EtalaseProduct) -> EtalaseProductOut:
    qr = qr_svc.ensure_qr(db, row)
    return _serialize(row, qr_token=qr.token)


def _get_owned(db: DbSession, product_id: UUID, brand_id: UUID) -> EtalaseProduct:
    row = db.scalar(
        select(EtalaseProduct).where(
            EtalaseProduct.id == product_id,
            EtalaseProduct.brand_id == brand_id,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Etalase product not found"
        )
    return row


def _assert_branch(db: DbSession, branch_id: UUID | None, brand_id: UUID) -> None:
    if branch_id is None:
        return
    exists = db.scalar(
        select(Branch.id).where(Branch.id == branch_id, Branch.brand_id == brand_id)
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid branch_id"
        )


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


@router.get("/api/etalase", response_model=list[EtalaseProductOut])
def list_etalase(
    current: RequireEtalaseAdmin,
    db: DbSession,
    branch_id: UUID | None = None,
    active_only: bool = False,
) -> list[EtalaseProductOut]:
    stmt = select(EtalaseProduct).where(EtalaseProduct.brand_id == current.brand_id)
    if branch_id is not None:
        stmt = stmt.where(EtalaseProduct.branch_id == branch_id)
    if active_only:
        stmt = stmt.where(EtalaseProduct.is_active.is_(True))
    rows = db.scalars(stmt.order_by(EtalaseProduct.created_at.desc())).all()
    out = [_serialize_with_qr(db, r) for r in rows]
    db.commit()  # persist lazy-ensure QR
    return out


@router.get("/api/etalase/{product_id}", response_model=EtalaseProductOut)
def get_etalase(
    product_id: UUID,
    current: RequireEtalaseAdmin,
    db: DbSession,
) -> EtalaseProductOut:
    row = _get_owned(db, product_id, current.brand_id)
    out = _serialize_with_qr(db, row)
    db.commit()
    return out


@router.post(
    "/api/etalase", response_model=EtalaseProductOut, status_code=status.HTTP_201_CREATED
)
async def create_etalase(
    current: RequireEtalaseAdmin,
    db: DbSession,
    name: str = Form(...),
    category: MockupCategory = Form(...),
    file: UploadFile = File(...),
    sku: str | None = Form(default=None),
    branch_id: UUID | None = Form(default=None),
) -> EtalaseProductOut:
    data = await file.read()
    content_type, ext = _read_and_validate_image(file, data)
    _assert_branch(db, branch_id, current.brand_id)

    product_id = uuid4()
    key = r2_service.build_etalase_object_key(
        brand_id=current.brand_id, product_id=product_id, ext=ext
    )
    try:
        r2_service.upload_bytes(key=key, data=data, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        logger.exception("etalase_store_failed key=%s", key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to store image: {exc}",
        ) from exc

    template_key = prompt_templates.template_key_for_category(category.value)
    row = EtalaseProduct(
        id=product_id,
        brand_id=current.brand_id,
        branch_id=branch_id,
        name=name.strip(),
        sku=(sku or "").strip() or None,
        category=category,
        image_r2_key=key,
        image_url=r2_service.resolve_image_url(key),
        prompt_template_key=template_key,
        is_active=True,
    )
    db.add(row)
    db.flush()
    qr = qr_svc.ensure_qr(db, row)
    db.commit()
    db.refresh(row)
    logger.info(
        "etalase_create id=%s brand_id=%s name=%s qr=%s",
        row.id,
        row.brand_id,
        row.name,
        qr.token,
    )
    return _serialize(row, qr_token=qr.token)


@router.patch("/api/etalase/{product_id}", response_model=EtalaseProductOut)
def update_etalase(
    product_id: UUID,
    body: EtalaseProductUpdate,
    current: RequireEtalaseAdmin,
    db: DbSession,
) -> EtalaseProductOut:
    row = _get_owned(db, product_id, current.brand_id)

    if body.name is not None:
        row.name = body.name.strip()
    if body.clear_sku:
        row.sku = None
    elif body.sku is not None:
        row.sku = body.sku.strip() or None
    if body.category is not None:
        row.category = body.category
        row.prompt_template_key = prompt_templates.template_key_for_category(
            body.category.value
        )
    if body.clear_branch:
        row.branch_id = None
    elif body.branch_id is not None:
        _assert_branch(db, body.branch_id, current.brand_id)
        row.branch_id = body.branch_id
    if body.is_active is not None:
        row.is_active = body.is_active

    qr = qr_svc.ensure_qr(db, row)
    db.commit()
    db.refresh(row)
    return _serialize(row, qr_token=qr.token)


@router.post(
    "/api/etalase/{product_id}/qr/regenerate", response_model=EtalaseProductOut
)
def regenerate_etalase_qr(
    product_id: UUID,
    current: RequireEtalaseAdmin,
    db: DbSession,
) -> EtalaseProductOut:
    row = _get_owned(db, product_id, current.brand_id)
    qr = qr_svc.regenerate_qr(db, row)
    db.commit()
    db.refresh(row)
    logger.info("etalase_qr_regen product_id=%s token=%s", row.id, qr.token)
    return _serialize(row, qr_token=qr.token)


@router.get("/api/etalase/{product_id}/qr.png")
def etalase_qr_png(
    product_id: UUID,
    current: RequireEtalaseAdmin,
    db: DbSession,
    base_url: str | None = Query(
        default=None,
        description="Origin FE, mis. http://localhost:3000 — di-encode ke QR",
    ),
) -> Response:
    row = _get_owned(db, product_id, current.brand_id)
    qr = qr_svc.ensure_qr(db, row)
    db.commit()
    origin = (base_url or "").rstrip("/")
    payload = f"{origin}/e/{qr.token}" if origin else f"/e/{qr.token}"
    png = qr_svc.qr_png_for_url(payload)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.put("/api/etalase/{product_id}/image", response_model=EtalaseProductOut)
async def replace_etalase_image(
    product_id: UUID,
    current: RequireEtalaseAdmin,
    db: DbSession,
    file: UploadFile = File(...),
) -> EtalaseProductOut:
    row = _get_owned(db, product_id, current.brand_id)
    data = await file.read()
    content_type, ext = _read_and_validate_image(file, data)
    old_key = row.image_r2_key
    new_key = f"brands/{current.brand_id}/etalase/{row.id}-{uuid4().hex[:10]}.{ext}"
    try:
        r2_service.upload_bytes(key=new_key, data=data, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store image",
        ) from exc

    row.image_r2_key = new_key
    row.image_url = r2_service.resolve_image_url(new_key)
    qr = qr_svc.ensure_qr(db, row)
    db.commit()
    db.refresh(row)
    if old_key and old_key != new_key:
        r2_service.delete_object(old_key)
    return _serialize(row, qr_token=qr.token)


@router.get("/api/etalase/{product_id}/file")
def download_etalase_file(
    product_id: UUID,
    current: RequireEtalaseAdmin,
    db: DbSession,
) -> Response:
    row = _get_owned(db, product_id, current.brand_id)
    try:
        data, content_type = r2_service.get_object_bytes(row.image_r2_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("etalase_stream_failed id=%s", product_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal ambil etalase dari storage",
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


@router.delete("/api/etalase/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_etalase(
    product_id: UUID,
    current: RequireEtalaseAdmin,
    db: DbSession,
) -> None:
    row = _get_owned(db, product_id, current.brand_id)
    key = row.image_r2_key
    db.delete(row)
    db.commit()
    r2_service.delete_object(key)


# ── Public resolve (scan QR dinding) ─────────────────────────────


@router.get(
    "/api/public/etalase/qr/{token}",
    response_model=PublicEtalaseProductOut,
)
def public_resolve_etalase_qr(token: str, db: DbSession) -> PublicEtalaseProductOut:
    resolved = qr_svc.resolve_by_token(db, token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QR etalase tidak valid atau sudah diganti",
        )
    qr, product = resolved
    brand = db.get(Brand, product.brand_id)
    url = r2_service.resolve_image_url(product.image_r2_key) or product.image_url
    return PublicEtalaseProductOut(
        product_id=product.id,
        brand_id=product.brand_id,
        brand_name=brand.name if brand else "Brand",
        brand_slug=brand.slug if brand else None,
        name=product.name,
        sku=product.sku,
        category=product.category.value
        if hasattr(product.category, "value")
        else str(product.category),
        image_url=url,
        is_active=product.is_active,
        qr_token=qr.token,
        qr_path=f"/e/{qr.token}",
    )


@router.get("/api/public/etalase/qr/{token}/png")
def public_etalase_qr_png(
    token: str,
    db: DbSession,
    base_url: str | None = Query(default=None),
) -> Response:
    resolved = qr_svc.resolve_by_token(db, token)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR not found")
    qr, _product = resolved
    origin = (base_url or "").rstrip("/")
    payload = f"{origin}/e/{qr.token}" if origin else f"/e/{qr.token}"
    png = qr_svc.qr_png_for_url(payload)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )
