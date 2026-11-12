"""Brand modules — studio vs fitting_store (independen).

Default register: hanya `studio` aktif. Fitting Store = opt-in owner
(atau superadmin di task-14).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BrandModule, BrandModuleKind, BrandModuleStatus


def get_module_row(
    db: Session, brand_id: UUID, module: BrandModuleKind
) -> BrandModule | None:
    return db.scalar(
        select(BrandModule).where(
            BrandModule.brand_id == brand_id,
            BrandModule.module == module,
        )
    )


def ensure_studio_seeded(db: Session, brand_id: UUID) -> BrandModule:
    """Brand lama tanpa row → seed studio aktif (sama pola wallet fitting)."""
    row = get_module_row(db, brand_id, BrandModuleKind.studio)
    if row is not None:
        return row
    row = BrandModule(
        brand_id=brand_id,
        module=BrandModuleKind.studio,
        status=BrandModuleStatus.active,
    )
    db.add(row)
    db.flush()
    return row


def seed_default_studio(db: Session, brand_id: UUID) -> BrandModule:
    """Dipakai saat register — hanya studio."""
    return ensure_studio_seeded(db, brand_id)


def is_module_active(db: Session, brand_id: UUID, module: BrandModuleKind) -> bool:
    if module == BrandModuleKind.studio:
        row = ensure_studio_seeded(db, brand_id)
    else:
        row = get_module_row(db, brand_id, module)
    return row is not None and row.status == BrandModuleStatus.active


def require_module(db: Session, brand_id: UUID, module: BrandModuleKind) -> None:
    if is_module_active(db, brand_id, module):
        return
    label = "Studio" if module == BrandModuleKind.studio else "Fitting Store"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Modul {label} tidak aktif untuk brand ini",
    )


def list_modules(db: Session, brand_id: UUID) -> list[BrandModule]:
    ensure_studio_seeded(db, brand_id)
    return list(
        db.scalars(
            select(BrandModule)
            .where(BrandModule.brand_id == brand_id)
            .order_by(BrandModule.module)
        ).all()
    )


def set_module_status(
    db: Session,
    brand_id: UUID,
    module: BrandModuleKind,
    status_value: BrandModuleStatus,
) -> BrandModule:
    row = get_module_row(db, brand_id, module)
    if row is None:
        row = BrandModule(
            brand_id=brand_id,
            module=module,
            status=status_value,
        )
        db.add(row)
    else:
        row.status = status_value
    db.flush()
    return row
