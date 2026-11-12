"""Generate / resolve QR token permanen untuk etalase_products."""

from __future__ import annotations

import io
import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EtalaseProduct, EtalaseQrCode

TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_token(length: int = 12) -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def active_qr(db: Session, product_id: UUID) -> EtalaseQrCode | None:
    return db.scalar(
        select(EtalaseQrCode)
        .where(
            EtalaseQrCode.product_id == product_id,
            EtalaseQrCode.revoked_at.is_(None),
        )
        .order_by(EtalaseQrCode.created_at.desc())
        .limit(1)
    )


def ensure_qr(db: Session, product: EtalaseProduct) -> EtalaseQrCode:
    existing = active_qr(db, product.id)
    if existing is not None:
        return existing
    for _ in range(40):
        token = generate_token()
        taken = db.scalar(select(EtalaseQrCode.id).where(EtalaseQrCode.token == token))
        if taken is None:
            row = EtalaseQrCode(
                id=uuid4(),
                brand_id=product.brand_id,
                product_id=product.id,
                token=token,
            )
            db.add(row)
            db.flush()
            return row
    raise RuntimeError("Gagal generate etalase QR token unik")


def regenerate_qr(db: Session, product: EtalaseProduct) -> EtalaseQrCode:
    now = _now()
    for row in db.scalars(
        select(EtalaseQrCode).where(
            EtalaseQrCode.product_id == product.id,
            EtalaseQrCode.revoked_at.is_(None),
        )
    ).all():
        row.revoked_at = now
    db.flush()
    return ensure_qr(db, product)


def resolve_by_token(db: Session, token: str) -> tuple[EtalaseQrCode, EtalaseProduct] | None:
    qr = db.scalar(
        select(EtalaseQrCode).where(
            EtalaseQrCode.token == token.strip().upper(),
            EtalaseQrCode.revoked_at.is_(None),
        )
    )
    if qr is None:
        return None
    product = db.get(EtalaseProduct, qr.product_id)
    if product is None or not product.is_active:
        return None
    return qr, product


def qr_png_bytes(token: str, *, box_size: int = 8, border: int = 2) -> bytes:
    """PNG QR mengarah ke path publik `/e/{token}` (host diisi FE / print)."""
    import qrcode

    # Payload relatif — print FE overlay full URL; PNG API pakai token saja sebagai fallback
    # Caller biasanya pass absolute URL.
    img = qrcode.make(token, box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_png_for_url(url: str, *, box_size: int = 8, border: int = 2) -> bytes:
    import qrcode

    img = qrcode.make(url, box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
