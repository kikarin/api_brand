"""Snapshot 1 frame — upload R2 + metadata."""

from __future__ import annotations

import logging
import uuid
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import DbSession, RequireDesignerPlus
from app.db.models import Snapshot, TryOnSession
from app.schemas.snapshots import SnapshotOut
from app.services import r2 as r2_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["snapshots"])

MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024


def _serialize(row: Snapshot) -> SnapshotOut:
    url = r2_service.resolve_image_url(row.r2_key) or row.image_url
    return SnapshotOut(
        id=row.id,
        session_id=row.session_id,
        brand_id=row.brand_id,
        mockup_id=row.mockup_id,
        r2_key=row.r2_key,
        image_url=url,
        # FE pakai endpoint auth-stream — lebih andal dari presign R2 di <img>
        content_path=f"/api/sessions/{row.session_id}/snapshots/{row.id}/file",
        created_at=row.created_at,
    )


def get_owned_snapshot(
    db: DbSession, *, session_id: UUID, snapshot_id: UUID, brand_id: UUID
) -> Snapshot:
    row = db.scalar(
        select(Snapshot).where(
            Snapshot.id == snapshot_id,
            Snapshot.session_id == session_id,
            Snapshot.brand_id == brand_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    return row


def stream_snapshot_file(row: Snapshot) -> Response:
    try:
        data, content_type = r2_service.get_object_bytes(row.r2_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("snapshot_stream_failed id=%s", row.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal ambil snapshot dari storage",
        ) from exc
    filename = row.r2_key.rsplit("/", 1)[-1] or f"{row.id}.jpg"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


def _owned_session(db: DbSession, session_id: UUID, brand_id: UUID) -> TryOnSession:
    row = db.scalar(
        select(TryOnSession).where(
            TryOnSession.id == session_id,
            TryOnSession.brand_id == brand_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return row


async def _read_image(file: UploadFile) -> tuple[bytes, str, str]:
    content_type = (file.content_type or "").lower()
    if content_type not in r2_service.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Snapshot harus JPEG/PNG/WebP",
        )
    data = await file.read()
    if not data or len(data) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Snapshot kosong atau > 5MB",
        )
    ext = r2_service.ALLOWED_CONTENT_TYPES[content_type]
    return data, content_type, ext


def create_snapshot_row(
    db: DbSession,
    *,
    session: TryOnSession,
    data: bytes,
    content_type: str,
    ext: str,
) -> Snapshot:
    snap_id = uuid.uuid4()
    key = f"brands/{session.brand_id}/snapshots/{session.id}/{snap_id}.{ext}"
    r2_service.upload_bytes(key=key, data=data, content_type=content_type)
    url = r2_service.resolve_image_url(key) or ""
    row = Snapshot(
        id=snap_id,
        session_id=session.id,
        brand_id=session.brand_id,
        mockup_id=session.mockup_id,
        r2_key=key,
        image_url=url,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "snapshot_create id=%s session_id=%s brand_id=%s",
        row.id,
        session.id,
        session.brand_id,
    )
    return row


@router.post("/api/sessions/{session_id}/snapshots", response_model=SnapshotOut)
async def upload_session_snapshot(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
    file: UploadFile = File(...),
) -> SnapshotOut:
    session = _owned_session(db, session_id, current.brand_id)
    data, content_type, ext = await _read_image(file)
    if not r2_service.storage_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is not available",
        )
    row = create_snapshot_row(
        db, session=session, data=data, content_type=content_type, ext=ext
    )
    return _serialize(row)


@router.get("/api/sessions/{session_id}/snapshots", response_model=list[SnapshotOut])
def list_session_snapshots(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> list[SnapshotOut]:
    _owned_session(db, session_id, current.brand_id)
    rows = db.scalars(
        select(Snapshot)
        .where(
            Snapshot.session_id == session_id,
            Snapshot.brand_id == current.brand_id,
        )
        .order_by(Snapshot.created_at.desc())
        .limit(50)
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/api/mockups/{mockup_id}/snapshots", response_model=list[SnapshotOut])
def list_mockup_snapshots(
    mockup_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> list[SnapshotOut]:
    rows = db.scalars(
        select(Snapshot)
        .where(
            Snapshot.mockup_id == mockup_id,
            Snapshot.brand_id == current.brand_id,
        )
        .order_by(Snapshot.created_at.desc())
        .limit(50)
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/api/sessions/{session_id}/snapshots/{snapshot_id}/file")
def download_session_snapshot_file(
    session_id: UUID,
    snapshot_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> Response:
    """Stream bytes via API (Bearer) — preview/unduh tanpa andalkan R2 public/CORS."""
    _owned_session(db, session_id, current.brand_id)
    row = get_owned_snapshot(
        db, session_id=session_id, snapshot_id=snapshot_id, brand_id=current.brand_id
    )
    return stream_snapshot_file(row)


def delete_snapshot_row(db: DbSession, row: Snapshot) -> None:
    key = row.r2_key
    db.delete(row)
    db.commit()
    r2_service.delete_object(key)
    logger.info("snapshot_delete id=%s key=%s", row.id, key)


def purge_session_snapshots(db: DbSession, session_id: UUID) -> int:
    """Hapus semua snapshot sesi (DB + R2). Dipanggil otomatis saat sesi ended."""
    rows = db.scalars(select(Snapshot).where(Snapshot.session_id == session_id)).all()
    if not rows:
        return 0
    keys = [r.r2_key for r in rows]
    for r in rows:
        db.delete(r)
    db.flush()
    for key in keys:
        r2_service.delete_object(key)
    logger.info(
        "snapshot_purge session_id=%s count=%s",
        session_id,
        len(keys),
    )
    return len(keys)


@router.delete("/api/sessions/{session_id}/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session_snapshot(
    session_id: UUID,
    snapshot_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> None:
    """Hapus metadata + object R2 (opsional). Purge massal otomatis saat sesi ended."""
    _owned_session(db, session_id, current.brand_id)
    row = get_owned_snapshot(
        db, session_id=session_id, snapshot_id=snapshot_id, brand_id=current.brand_id
    )
    delete_snapshot_row(db, row)
