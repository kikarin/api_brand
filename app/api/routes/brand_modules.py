"""Brand modules API — list + enable/suspend (owner).

Default register: studio saja. Fitting Store opt-in di sini.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AuthUser, DbSession, RequireOwner
from app.db.models import BrandModuleKind, BrandModuleStatus
from app.schemas.etalase import BrandModuleOut
from app.services import brand_modules as modules_svc

router = APIRouter(prefix="/api/brand/modules", tags=["brand-modules"])


@router.get("", response_model=list[BrandModuleOut])
def list_brand_modules(current: AuthUser, db: DbSession) -> list[BrandModuleOut]:
    rows = modules_svc.list_modules(db, current.brand_id)
    db.commit()  # persist lazy studio seed bila ada
    return [
        BrandModuleOut(
            id=r.id,
            brand_id=r.brand_id,
            module=r.module.value,
            status=r.status.value,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/{module}/enable", response_model=BrandModuleOut)
def enable_module(
    module: str,
    current: RequireOwner,
    db: DbSession,
) -> BrandModuleOut:
    try:
        kind = BrandModuleKind(module)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="module harus studio atau fitting_store",
        ) from exc
    row = modules_svc.set_module_status(
        db, current.brand_id, kind, BrandModuleStatus.active
    )
    db.commit()
    db.refresh(row)
    return BrandModuleOut(
        id=row.id,
        brand_id=row.brand_id,
        module=row.module.value,
        status=row.status.value,
        created_at=row.created_at,
    )


@router.post("/{module}/suspend", response_model=BrandModuleOut)
def suspend_module(
    module: str,
    current: RequireOwner,
    db: DbSession,
) -> BrandModuleOut:
    try:
        kind = BrandModuleKind(module)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="module harus studio atau fitting_store",
        ) from exc
    if kind == BrandModuleKind.studio:
        # Studio adalah default product — boleh suspend tapi dokumentasikan
        pass
    row = modules_svc.set_module_status(
        db, current.brand_id, kind, BrandModuleStatus.suspended
    )
    db.commit()
    db.refresh(row)
    return BrandModuleOut(
        id=row.id,
        brand_id=row.brand_id,
        module=row.module.value,
        status=row.status.value,
        created_at=row.created_at,
    )
