from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, RequireAnyMember, RequireDesignerPlus, require_brand_module
from app.db.models import BrandModuleKind, Collection
from app.schemas.mockups import CollectionCreate, CollectionOut, CollectionUpdate

router = APIRouter(
    prefix="/api/collections",
    tags=["collections"],
    dependencies=[Depends(require_brand_module(BrandModuleKind.studio))],
)


def _get_owned(db: DbSession, collection_id: UUID, brand_id: UUID) -> Collection:
    row = db.scalar(
        select(Collection).where(
            Collection.id == collection_id,
            Collection.brand_id == brand_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return row


@router.get("", response_model=list[CollectionOut])
def list_collections(current: RequireAnyMember, db: DbSession) -> list[Collection]:
    rows = db.scalars(
        select(Collection)
        .where(Collection.brand_id == current.brand_id)
        .order_by(Collection.created_at.desc())
    ).all()
    return list(rows)


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
def create_collection(
    body: CollectionCreate,
    current: RequireDesignerPlus,
    db: DbSession,
) -> Collection:
    row = Collection(brand_id=current.brand_id, name=body.name.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{collection_id}", response_model=CollectionOut)
def update_collection(
    collection_id: UUID,
    body: CollectionUpdate,
    current: RequireDesignerPlus,
    db: DbSession,
) -> Collection:
    row = _get_owned(db, collection_id, current.brand_id)
    row.name = body.name.strip()
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collection(
    collection_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> None:
    row = _get_owned(db, collection_id, current.brand_id)
    db.delete(row)
    db.commit()
