"""Invite / list user toko (store_admin + fitting_attendant).

Owner atau store_admin. Attendant wajib branch_id.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession, RequireStoreUserAdmin
from app.core.security import hash_password
from app.db.models import Branch, BrandModuleKind, User, UserRole
from app.schemas.auth import StoreUserCreate, StoreUserOut, UserOut
from app.services import brand_modules as modules_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brand/users", tags=["brand-users"])

STORE_ROLES = {UserRole.store_admin, UserRole.fitting_attendant}


def _serialize(u: User) -> StoreUserOut:
    return StoreUserOut(
        id=u.id,
        email=u.email,
        role=u.role,
        brand_id=u.brand_id,
        branch_id=u.branch_id,
        created_at=u.created_at,
    )


@router.get("/me", response_model=UserOut)
def get_me_extended(current: AuthUser) -> UserOut:
    """Detail user termasuk branch_id (FE nav / redirect)."""
    return UserOut.model_validate(current.user)


@router.get("", response_model=list[StoreUserOut])
def list_brand_users(
    current: RequireStoreUserAdmin,
    db: DbSession,
    store_only: bool = True,
) -> list[StoreUserOut]:
    stmt = select(User).where(User.brand_id == current.brand_id)
    if store_only:
        stmt = stmt.where(User.role.in_(list(STORE_ROLES)))
    rows = db.scalars(stmt.order_by(User.created_at.desc())).all()
    return [_serialize(u) for u in rows]


@router.post("", response_model=StoreUserOut, status_code=status.HTTP_201_CREATED)
def create_store_user(
    body: StoreUserCreate,
    current: RequireStoreUserAdmin,
    db: DbSession,
) -> StoreUserOut:
    if body.role not in STORE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya boleh create store_admin atau fitting_attendant",
        )

    # Toko butuh modul fitting_store
    modules_svc.require_module(
        db, current.brand_id, BrandModuleKind.fitting_store
    )

    # store_admin tidak boleh create sesama store_admin? Owner saja untuk store_admin.
    if (
        body.role == UserRole.store_admin
        and current.role != UserRole.owner
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya owner yang boleh membuat store_admin",
        )

    email = body.email.lower().strip()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    branch_id = body.branch_id
    if body.role == UserRole.fitting_attendant:
        if branch_id is None:
            # Default: cabang pertama brand
            first = db.scalar(
                select(Branch)
                .where(Branch.brand_id == current.brand_id)
                .order_by(Branch.created_at.asc())
                .limit(1)
            )
            if first is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Attendant butuh branch — buat cabang dulu di Fitting",
                )
            branch_id = first.id
        else:
            br = db.scalar(
                select(Branch).where(
                    Branch.id == branch_id,
                    Branch.brand_id == current.brand_id,
                )
            )
            if br is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid branch_id",
                )
    elif branch_id is not None:
        br = db.scalar(
            select(Branch).where(
                Branch.id == branch_id,
                Branch.brand_id == current.brand_id,
            )
        )
        if br is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid branch_id",
            )

    user = User(
        brand_id=current.brand_id,
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
        branch_id=branch_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "store_user_create id=%s role=%s brand_id=%s by=%s",
        user.id,
        user.role.value,
        user.brand_id,
        current.user.id,
    )
    return _serialize(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_store_user(
    user_id: UUID,
    current: RequireStoreUserAdmin,
    db: DbSession,
) -> None:
    row = db.scalar(
        select(User).where(User.id == user_id, User.brand_id == current.brand_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if row.role not in STORE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya bisa hapus user toko",
        )
    if row.id == current.user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tidak bisa hapus akun sendiri",
        )
    if row.role == UserRole.store_admin and current.role != UserRole.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya owner yang boleh hapus store_admin",
        )
    db.delete(row)
    db.commit()
