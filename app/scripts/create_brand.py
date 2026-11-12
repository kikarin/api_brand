"""Buat brand + owner + kuota (pengganti sementara dashboard platform).

  cd platform-api
  .venv/bin/python -m app.scripts.create_brand \\
    --name "Acme Wear" \\
    --email owner@acme.test \\
    --password 'secret123' \\
    --studio-quota 50 \\
    --fitting-quota 10

Fitting module default nonaktif kecuali --enable-fitting.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.core.slug import slugify
from app.db.models import (
    Brand,
    BrandModuleKind,
    BrandModuleStatus,
    BrandStatus,
    QuotaChannel,
    QuotaWallet,
    User,
    UserRole,
)
from app.db.session import SessionLocal
from app.services import brand_modules as modules_svc


def _unique_slug(db, base: str) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while db.scalar(select(Brand.id).where(Brand.slug == candidate)) is not None:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="Create brand + owner account")
    parser.add_argument("--name", required=True, help="Nama brand")
    parser.add_argument("--email", required=True, help="Email owner")
    parser.add_argument("--password", required=True, help="Password owner (min 8)")
    parser.add_argument("--studio-quota", type=int, default=50)
    parser.add_argument("--fitting-quota", type=int, default=10)
    parser.add_argument(
        "--enable-fitting",
        action="store_true",
        help="Aktifkan modul fitting_store",
    )
    args = parser.parse_args()

    if len(args.password) < 8:
        raise SystemExit("Password minimal 8 karakter")
    if args.studio_quota < 0 or args.fitting_quota < 0:
        raise SystemExit("Quota tidak boleh negatif")

    email = args.email.lower().strip()
    db = SessionLocal()
    try:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise SystemExit(f"Email sudah terpakai: {email}")

        now = datetime.now(timezone.utc)
        brand = Brand(
            name=args.name.strip(),
            slug=_unique_slug(db, args.name),
            status=BrandStatus.active,
            plan="starter",
        )
        db.add(brand)
        db.flush()

        user = User(
            brand_id=brand.id,
            email=email,
            password_hash=hash_password(args.password),
            role=UserRole.owner,
        )
        db.add(user)
        db.add(
            QuotaWallet(
                brand_id=brand.id,
                channel=QuotaChannel.studio,
                balance=args.studio_quota,
                period_start=now,
                period_end=now + timedelta(days=30),
            )
        )
        db.add(
            QuotaWallet(
                brand_id=brand.id,
                channel=QuotaChannel.fitting_room,
                balance=args.fitting_quota,
                period_start=now,
                period_end=now + timedelta(days=30),
            )
        )
        modules_svc.seed_default_studio(db, brand.id)
        if args.enable_fitting:
            modules_svc.set_module_status(
                db,
                brand.id,
                BrandModuleKind.fitting_store,
                BrandModuleStatus.active,
            )
        db.commit()
        print(f"OK brand={brand.slug} id={brand.id}")
        print(f"owner={email} password=(hidden)")
        print(f"studio_quota={args.studio_quota} fitting_quota={args.fitting_quota}")
        print(f"fitting_module={'active' if args.enable_fitting else 'inactive'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
