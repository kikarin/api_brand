from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import get_settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(
    *,
    user_id: UUID,
    brand_id: UUID,
    role: str,
    email: str,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "typ": "brand",
        "sub": str(user_id),
        "brand_id": str(brand_id),
        "role": role,
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    typ = payload.get("typ")
    # Token lama tanpa typ tetap diterima sebagai brand
    if typ is not None and typ != "brand":
        raise InvalidTokenError("Not a brand access token")
    return payload


def create_platform_access_token(*, admin_id: UUID, email: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "typ": "platform",
        "sub": str(admin_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_platform_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("typ") != "platform":
        raise InvalidTokenError("Not a platform access token")
    return payload


def create_share_access_token(
    *,
    share_token: str,
    share_link_id: UUID,
    expires_at: datetime,
) -> str:
    """Token singkat setelah password benar — biar reload tidak minta password lagi."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    # Cap 24 jam atau sisa umur link (mana lebih pendek)
    cap = now + timedelta(hours=24)
    if exp > cap:
        exp = cap
    if exp <= now:
        exp = now + timedelta(minutes=5)
    payload: dict[str, Any] = {
        "typ": "share_access",
        "share_token": share_token,
        "share_link_id": str(share_link_id),
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_share_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("typ") != "share_access":
        raise InvalidTokenError("Not a share access token")
    return payload
