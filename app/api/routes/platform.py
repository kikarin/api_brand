"""Platform superadmin — tenant, kuota, modul. Auth terpisah dari users brand."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from app.api.deps import DbSession, RequirePlatformAdmin
from app.core.security import (
    create_platform_access_token,
    hash_password,
    verify_password,
)
from app.core.slug import slugify
from app.db.models import (
    Brand,
    BrandModuleKind,
    BrandModuleStatus,
    BrandStatus,
    PlatformAdmin,
    QuotaChannel,
    QuotaWallet,
    User,
    UserRole,
)
from app.schemas.platform import (
    PlatformAdminOut,
    PlatformAuthResponse,
    PlatformBrandCreate,
    PlatformBrandOut,
    PlatformBrandPatch,
    PlatformLoginRequest,
    PlatformModuleOut,
    PlatformQuotaAdjust,
    PlatformWalletOut,
)
from app.services import brand_modules as modules_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform", tags=["platform"])

ALLOWED_QUOTA = {QuotaChannel.studio, QuotaChannel.fitting_room}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unique_slug(db: DbSession, base: str) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while db.scalar(select(Brand.id).where(Brand.slug == candidate)) is not None:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def _serialize_brand(db: DbSession, brand: Brand) -> PlatformBrandOut:
    owner = db.scalar(
        select(User).where(
            User.brand_id == brand.id,
            User.role == UserRole.owner,
        )
    )
    wallets = db.scalars(
        select(QuotaWallet).where(QuotaWallet.brand_id == brand.id)
    ).all()
    modules = modules_svc.list_modules(db, brand.id)
    return PlatformBrandOut(
        id=brand.id,
        name=brand.name,
        slug=brand.slug,
        status=brand.status,
        plan=brand.plan,
        created_at=brand.created_at,
        owner_email=owner.email if owner else None,
        wallets=[
            PlatformWalletOut(channel=w.channel, balance=w.balance)
            for w in wallets
            if w.channel in ALLOWED_QUOTA
        ],
        modules=[
            PlatformModuleOut(module=m.module, status=m.status) for m in modules
        ],
    )


@router.post("/auth/login", response_model=PlatformAuthResponse)
def platform_login(body: PlatformLoginRequest, db: DbSession) -> PlatformAuthResponse:
    email = body.email.lower().strip()
    admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == email))
    if admin is None or not verify_password(body.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_platform_access_token(admin_id=admin.id, email=admin.email)
    return PlatformAuthResponse(
        access_token=token,
        admin=PlatformAdminOut.model_validate(admin),
    )


@router.get("/me", response_model=PlatformAdminOut)
def platform_me(current: RequirePlatformAdmin) -> PlatformAdminOut:
    return PlatformAdminOut.model_validate(current.admin)


@router.get("/brands", response_model=list[PlatformBrandOut])
def list_brands(current: RequirePlatformAdmin, db: DbSession) -> list[PlatformBrandOut]:
    _ = current
    rows = db.scalars(select(Brand).order_by(Brand.created_at.desc())).all()
    return [_serialize_brand(db, b) for b in rows]


@router.get("/brands/{brand_id}", response_model=PlatformBrandOut)
def get_brand(
    brand_id: UUID,
    current: RequirePlatformAdmin,
    db: DbSession,
) -> PlatformBrandOut:
    _ = current
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    return _serialize_brand(db, brand)


@router.post("/brands", response_model=PlatformBrandOut, status_code=status.HTTP_201_CREATED)
def create_brand(
    body: PlatformBrandCreate,
    current: RequirePlatformAdmin,
    db: DbSession,
) -> PlatformBrandOut:
    email = body.owner_email.lower().strip()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner email already registered",
        )

    now = _now()
    brand = Brand(
        name=body.name.strip(),
        slug=_unique_slug(db, body.name),
        status=BrandStatus.active,
        plan="starter",
    )
    db.add(brand)
    db.flush()

    owner = User(
        brand_id=brand.id,
        email=email,
        password_hash=hash_password(body.owner_password),
        role=UserRole.owner,
    )
    db.add(owner)
    db.add(
        QuotaWallet(
            brand_id=brand.id,
            channel=QuotaChannel.studio,
            balance=body.studio_quota,
            period_start=now,
            period_end=now + timedelta(days=30),
        )
    )
    db.add(
        QuotaWallet(
            brand_id=brand.id,
            channel=QuotaChannel.fitting_room,
            balance=body.fitting_quota,
            period_start=now,
            period_end=now + timedelta(days=30),
        )
    )
    modules_svc.seed_default_studio(db, brand.id)
    if body.enable_fitting:
        modules_svc.set_module_status(
            db, brand.id, BrandModuleKind.fitting_store, BrandModuleStatus.active
        )
    db.commit()
    db.refresh(brand)
    logger.info(
        "platform_brand_create brand_id=%s slug=%s by=%s",
        brand.id,
        brand.slug,
        current.id,
    )
    return _serialize_brand(db, brand)


@router.patch("/brands/{brand_id}", response_model=PlatformBrandOut)
def patch_brand(
    brand_id: UUID,
    body: PlatformBrandPatch,
    current: RequirePlatformAdmin,
    db: DbSession,
) -> PlatformBrandOut:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    brand.status = body.status
    db.commit()
    db.refresh(brand)
    logger.info(
        "platform_brand_patch brand_id=%s status=%s by=%s",
        brand.id,
        brand.status.value,
        current.id,
    )
    return _serialize_brand(db, brand)


@router.post("/brands/{brand_id}/quota", response_model=PlatformBrandOut)
def adjust_quota(
    brand_id: UUID,
    body: PlatformQuotaAdjust,
    current: RequirePlatformAdmin,
    db: DbSession,
) -> PlatformBrandOut:
    if body.channel not in ALLOWED_QUOTA:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel harus studio atau fitting_room",
        )
    if body.delta == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="delta tidak boleh 0",
        )

    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")

    wallet = db.scalar(
        select(QuotaWallet).where(
            QuotaWallet.brand_id == brand_id,
            QuotaWallet.channel == body.channel,
        )
    )
    now = _now()
    if wallet is None:
        if body.delta < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wallet belum ada; tidak bisa mengurangi",
            )
        wallet = QuotaWallet(
            brand_id=brand_id,
            channel=body.channel,
            balance=body.delta,
            period_start=now,
            period_end=now + timedelta(days=30),
        )
        db.add(wallet)
    else:
        new_balance = wallet.balance + body.delta
        if new_balance < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Balance tidak boleh negatif (saat ini {wallet.balance})",
            )
        wallet.balance = new_balance

    db.commit()
    logger.info(
        "platform_quota brand_id=%s channel=%s delta=%s by=%s",
        brand_id,
        body.channel.value,
        body.delta,
        current.id,
    )
    return _serialize_brand(db, brand)


@router.post(
    "/brands/{brand_id}/modules/{module}/enable",
    response_model=PlatformModuleOut,
)
def enable_module(
    brand_id: UUID,
    module: BrandModuleKind,
    current: RequirePlatformAdmin,
    db: DbSession,
) -> PlatformModuleOut:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    row = modules_svc.set_module_status(
        db, brand_id, module, BrandModuleStatus.active
    )
    db.commit()
    logger.info(
        "platform_module_enable brand_id=%s module=%s by=%s",
        brand_id,
        module.value,
        current.id,
    )
    return PlatformModuleOut(module=row.module, status=row.status)


@router.post(
    "/brands/{brand_id}/modules/{module}/suspend",
    response_model=PlatformModuleOut,
)
def suspend_module(
    brand_id: UUID,
    module: BrandModuleKind,
    current: RequirePlatformAdmin,
    db: DbSession,
) -> PlatformModuleOut:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    row = modules_svc.set_module_status(
        db, brand_id, module, BrandModuleStatus.suspended
    )
    db.commit()
    logger.info(
        "platform_module_suspend brand_id=%s module=%s by=%s",
        brand_id,
        module.value,
        current.id,
    )
    return PlatformModuleOut(module=row.module, status=row.status)
