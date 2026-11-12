"""Seed brand demo untuk lokal.

  python -m app.scripts.seed_dev

Login: owner@gmail.com / owner123
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
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

EMAIL = "owner@gmail.com"
PASSWORD = "owner123"
BRAND_NAME = "Demo Brand"
BRAND_SLUG = "demo-brand"


def main() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        brand = db.scalar(select(Brand).where(Brand.slug == BRAND_SLUG))
        if brand is None:
            brand = Brand(
                name=BRAND_NAME,
                slug=BRAND_SLUG,
                status=BrandStatus.active,
                plan="starter",
            )
            db.add(brand)
            db.flush()
        else:
            print(f"Pakai brand slug={BRAND_SLUG} yang sudah ada")

        user = db.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            # Owner lama di brand demo (email diganti di seed)
            user = db.scalar(
                select(User).where(
                    User.brand_id == brand.id,
                    User.role == UserRole.owner,
                )
            )
        if user is not None:
            user.email = EMAIL
            user.password_hash = hash_password(PASSWORD)
            user.brand_id = brand.id
            user.role = UserRole.owner
            print(f"Update owner → {EMAIL}")
        else:
            user = User(
                brand_id=brand.id,
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
                role=UserRole.owner,
            )
            db.add(user)
            print(f"Buat owner {EMAIL}")

        for channel, balance in (
            (QuotaChannel.studio, 50),
            (QuotaChannel.fitting_room, 20),
        ):
            wallet = db.scalar(
                select(QuotaWallet).where(
                    QuotaWallet.brand_id == brand.id,
                    QuotaWallet.channel == channel,
                )
            )
            if wallet is None:
                db.add(
                    QuotaWallet(
                        brand_id=brand.id,
                        channel=channel,
                        balance=balance,
                        period_start=now,
                        period_end=now + timedelta(days=30),
                    )
                )

        modules_svc.seed_default_studio(db, brand.id)
        modules_svc.set_module_status(
            db, brand.id, BrandModuleKind.fitting_store, BrandModuleStatus.active
        )
        db.commit()
        print(f"OK brand={brand.slug} user={EMAIL} password={PASSWORD}")
        print("Modul: studio + fitting_store aktif")
    finally:
        db.close()


if __name__ == "__main__":
    main()
