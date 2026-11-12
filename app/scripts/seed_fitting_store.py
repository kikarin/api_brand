"""Seed Fitting Store demo: branch, store users, etalase sample.

  python -m app.scripts.seed_fitting_store

Staff login (fitting-web):
  store_admin@demo.brand / store1234
  attendant@demo.brand / staff1234

Brand slug: demo-brand (owner tetap dari seed_dev)
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import (
    Brand,
    BrandModuleKind,
    BrandModuleStatus,
    Branch,
    EtalaseProduct,
    MockupCategory,
    QuotaChannel,
    QuotaWallet,
    User,
    UserRole,
)
from app.db.session import SessionLocal
from app.services import brand_modules as modules_svc
from app.services import etalase_qr as qr_svc
from app.services import prompt_templates
from app.services import r2 as r2_service

BRAND_SLUG = "demo-brand"
ADMIN_EMAIL = "store_admin@demo.brand"
ADMIN_PASSWORD = "store1234"
ATTENDANT_EMAIL = "attendant@demo.brand"
ATTENDANT_PASSWORD = "staff1234"
BRANCH_NAME = "Outlet Demo"


def _png(color: tuple[int, int, int], size: int = 64) -> bytes:
    """Minimal solid PNG."""
    r, g, b = color
    raw = b"".join(b"\x00" + bytes([r, g, b]) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


SAMPLES = [
    ("Blazer Navy", MockupCategory.outer, (30, 60, 120)),
    ("Knit Cream", MockupCategory.top, (220, 200, 170)),
    ("Trouser Slate", MockupCategory.bottom, (70, 80, 95)),
    ("Shirt White", MockupCategory.top, (240, 240, 245)),
    ("Coat Camel", MockupCategory.outer, (180, 130, 80)),
    ("Dress Olive", MockupCategory.dress, (90, 110, 70)),
    ("Dress Rust Maxi", MockupCategory.dress, (196, 98, 45)),
]


def main() -> None:
    db = SessionLocal()
    try:
        brand = db.scalar(select(Brand).where(Brand.slug == BRAND_SLUG))
        if brand is None:
            raise SystemExit(f"Brand slug={BRAND_SLUG} belum ada — jalankan seed_dev dulu")

        modules_svc.seed_default_studio(db, brand.id)
        modules_svc.set_module_status(
            db, brand.id, BrandModuleKind.fitting_store, BrandModuleStatus.active
        )

        now = datetime.now(timezone.utc)
        wallet = db.scalar(
            select(QuotaWallet).where(
                QuotaWallet.brand_id == brand.id,
                QuotaWallet.channel == QuotaChannel.fitting_room,
            )
        )
        if wallet is None:
            db.add(
                QuotaWallet(
                    brand_id=brand.id,
                    channel=QuotaChannel.fitting_room,
                    balance=50,
                    period_start=now,
                    period_end=now + timedelta(days=30),
                )
            )
        elif wallet.balance < 10:
            wallet.balance = 50

        branch = db.scalar(
            select(Branch).where(Branch.brand_id == brand.id, Branch.name == BRANCH_NAME)
        )
        if branch is None:
            branch = Branch(brand_id=brand.id, name=BRANCH_NAME, max_ai_rooms=4)
            db.add(branch)
            db.flush()
            print(f"Buat branch {BRANCH_NAME} max_ai_rooms=4")
        else:
            branch.max_ai_rooms = max(branch.max_ai_rooms, 4)
            print(f"Pakai branch {branch.name}")

        def upsert_user(email: str, password: str, role: UserRole) -> User:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    brand_id=brand.id,
                    email=email,
                    password_hash=hash_password(password),
                    role=role,
                    branch_id=branch.id if role == UserRole.fitting_attendant else None,
                )
                db.add(user)
                print(f"Buat {role.value} {email}")
            else:
                user.password_hash = hash_password(password)
                user.role = role
                user.brand_id = brand.id
                if role == UserRole.fitting_attendant:
                    user.branch_id = branch.id
                print(f"Update {role.value} {email}")
            return user

        upsert_user(ADMIN_EMAIL, ADMIN_PASSWORD, UserRole.store_admin)
        upsert_user(ATTENDANT_EMAIL, ATTENDANT_PASSWORD, UserRole.fitting_attendant)

        created = 0
        updated = 0
        for name, category, color in SAMPLES:
            product = db.scalar(
                select(EtalaseProduct).where(
                    EtalaseProduct.brand_id == brand.id,
                    EtalaseProduct.name == name,
                )
            )
            template_key = prompt_templates.template_key_for_category(category.value)
            if product is None:
                product = EtalaseProduct(
                    brand_id=brand.id,
                    branch_id=branch.id,
                    name=name,
                    sku=name.upper().replace(" ", "-")[:20],
                    category=category,
                    image_r2_key="pending",
                    image_url="",
                    prompt_template_key=template_key,
                    is_active=True,
                )
                db.add(product)
                db.flush()
                key = r2_service.build_etalase_object_key(
                    brand_id=brand.id, product_id=product.id, ext="png"
                )
                r2_service.upload_bytes(
                    key=key, data=_png(color), content_type="image/png"
                )
                product.image_r2_key = key
                product.image_url = r2_service.resolve_image_url(key) or ""
                qr_svc.ensure_qr(db, product)
                created += 1
            else:
                if product.category != category or product.prompt_template_key != template_key:
                    product.category = category
                    product.prompt_template_key = template_key
                    updated += 1
                qr_svc.ensure_qr(db, product)
        print(f"Etalase seed: created={created} updated={updated}")

        db.commit()
        print(
            f"OK fitting seed brand={BRAND_SLUG}\n"
            f"  admin {ADMIN_EMAIL} / {ADMIN_PASSWORD}\n"
            f"  attendant {ATTENDANT_EMAIL} / {ATTENDANT_PASSWORD}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
