"""Seed platform superadmin (idempotent / upsert).

  python -m app.scripts.seed_platform_admin

Login: superadmin@gmail.com / Sewdaq123
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import PlatformAdmin
from app.db.session import SessionLocal

EMAIL = "superadmin@gmail.com"
PASSWORD = "Sewdaq123"


def main() -> None:
    db = SessionLocal()
    try:
        admin = db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == EMAIL))
        if admin is None:
            # Email diganti di seed — update admin yang sudah ada (biasanya satu)
            admin = db.scalar(select(PlatformAdmin).order_by(PlatformAdmin.created_at))
        if admin is not None:
            admin.email = EMAIL
            admin.password_hash = hash_password(PASSWORD)
            db.commit()
            db.refresh(admin)
            print(f"Update platform_admin={EMAIL} password={PASSWORD} id={admin.id}")
            return

        admin = PlatformAdmin(
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print(f"OK platform_admin={EMAIL} password={PASSWORD} id={admin.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
