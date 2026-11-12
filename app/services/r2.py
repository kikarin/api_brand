from __future__ import annotations

import logging
import mimetypes
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

LOCAL_ROOT = Path(__file__).resolve().parents[2] / ".local-data" / "uploads"


@lru_cache
def _client():
    settings = get_settings()
    if not settings.r2_account_id or not settings.r2_access_key_id:
        return None
    endpoint = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def r2_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket
    )


def storage_ready(settings: Settings | None = None) -> bool:
    """R2 cloud atau fallback lokal (dev tanpa kredensial)."""
    _ = settings
    return True


def local_exists(key: str) -> bool:
    return (LOCAL_ROOT / key).is_file()


def build_object_key(*, brand_id: UUID, mockup_id: UUID, ext: str) -> str:
    return f"brands/{brand_id}/mockups/{mockup_id}.{ext}"


def build_etalase_object_key(*, brand_id: UUID, product_id: UUID, ext: str) -> str:
    """Path R2 terpisah dari mockups — jangan campur katalog."""
    return f"brands/{brand_id}/etalase/{product_id}.{ext}"


def _local_path(key: str) -> Path:
    path = LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _local_media_url(key: str) -> str:
    """URL untuk file di disk. Relative biar FE (Vercel) bisa rewrite same-origin."""
    settings = get_settings()
    base = (settings.public_api_base_url or "").rstrip("/")
    if base:
        return f"{base}/local-media/{key}"
    return f"/local-media/{key}"


def upload_bytes(*, key: str, data: bytes, content_type: str) -> None:
    """Simpan lokal selalu; R2 best-effort jika kredensial ada."""
    settings = get_settings()
    target = _local_path(key)
    target.write_bytes(data)
    meta = target.with_suffix(target.suffix + ".ctype")
    meta.write_text(content_type or "application/octet-stream", encoding="utf-8")

    if r2_configured(settings) and _client() is not None:
        try:
            _client().put_object(
                Bucket=settings.r2_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except Exception:
            logger.exception("r2_put_failed key=%s — kept local copy only", key)
            return
        return

    logger.info("local_upload key=%s bytes=%s", key, len(data))


def delete_object(key: str) -> None:
    """Best-effort delete — jangan gagalkan request bisnis jika storage error."""
    settings = get_settings()
    path = LOCAL_ROOT / key
    try:
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".ctype").unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete local object key=%s", key, exc_info=True)

    if not r2_configured(settings) or _client() is None:
        return
    try:
        _client().delete_object(Bucket=settings.r2_bucket, Key=key)
    except ClientError:
        logger.warning("Failed to delete R2 object key=%s", key, exc_info=True)


def resolve_image_url(key: str, *, expires_in: int = 3600) -> str:
    settings = get_settings()

    # CDN / public bucket R2 — prioritas tertinggi (aman di HTTPS Vercel)
    if settings.r2_public_base_url:
        return f"{settings.r2_public_base_url.rstrip('/')}/{key}"

    # File ada di disk server (seed / mirror) → local-media (relative atau PUBLIC_API_BASE_URL)
    if local_exists(key):
        return _local_media_url(key)

    if r2_configured(settings) and _client() is not None:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    return _local_media_url(key)


def get_object_bytes(key: str) -> tuple[bytes, str]:
    """Return (body, content_type) for streaming preview/download via API."""
    settings = get_settings()

    if local_exists(key):
        path = LOCAL_ROOT / key
        meta = path.with_suffix(path.suffix + ".ctype")
        content_type = (
            meta.read_text(encoding="utf-8").strip()
            if meta.is_file()
            else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        return path.read_bytes(), content_type

    if r2_configured(settings) and _client() is not None:
        try:
            obj = _client().get_object(Bucket=settings.r2_bucket, Key=key)
            body = obj["Body"].read()
            content_type = obj.get("ContentType") or "application/octet-stream"
            return body, content_type
        except ClientError as exc:
            logger.warning("r2_get_failed key=%s err=%s", key, exc)
            raise FileNotFoundError(key) from exc

    raise FileNotFoundError(key)
