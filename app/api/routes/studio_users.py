"""Owner invite designer / viewer ke brand yang sama.

Kuota sesi tetap di wallet brand — designer tidak punya kuota terpisah.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, RequireOwner
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.schemas.auth import StudioUserCreate, StudioUserOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brand/studio-users", tags=["studio-users"])

STUDIO_MEMBER_ROLES = {UserRole.owner, UserRole.designer, UserRole.viewer}
INVITABLE_ROLES = {UserRole.designer, UserRole.viewer}


def _serialize(u: User) -> StudioUserOut:
    return StudioUserOut(
        id=u.id,
        email=u.email,
        role=u.role,
        brand_id=u.brand_id,
        created_at=u.created_at,
    )


@router.get("", response_model=list[StudioUserOut])
def list_studio_users(current: RequireOwner, db: DbSession) -> list[StudioUserOut]:
    rows = db.scalars(
        select(User)
        .where(
            User.brand_id == current.brand_id,
            User.role.in_(list(STUDIO_MEMBER_ROLES)),
        )
        .order_by(User.created_at.asc())
    ).all()
    return [_serialize(u) for u in rows]


@router.post("", response_model=StudioUserOut, status_code=status.HTTP_201_CREATED)
def create_studio_user(
    body: StudioUserCreate,
    current: RequireOwner,
    db: DbSession,
) -> StudioUserOut:
    if body.role not in INVITABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya boleh create designer atau viewer",
        )

    email = body.email.lower().strip()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        brand_id=current.brand_id,
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
        branch_id=None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "studio_user_create id=%s role=%s brand_id=%s by=%s",
        user.id,
        user.role.value,
        user.brand_id,
        current.user.id,
    )
    return _serialize(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_studio_user(
    user_id: UUID,
    current: RequireOwner,
    db: DbSession,
) -> None:
    row = db.scalar(
        select(User).where(User.id == user_id, User.brand_id == current.brand_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if row.role not in INVITABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya bisa hapus designer atau viewer",
        )
    if row.id == current.user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tidak bisa hapus akun sendiri",
        )
    db.delete(row)
    db.commit()
    logger.info(
        "studio_user_delete id=%s brand_id=%s by=%s",
        user_id,
        current.brand_id,
        current.user.id,
    )
