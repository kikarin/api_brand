"""Share link privat — create (auth) + resolve/start (public)."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, RequireDesignerPlus
from app.core.config import get_settings
from app.core.security import (
    create_share_access_token,
    decode_share_access_token,
    hash_password,
    verify_password,
)
from jwt.exceptions import InvalidTokenError
from app.db.models import (
    Brand,
    BrandModuleKind,
    Mockup,
    QuotaWallet,
    SessionChannel,
    SessionStatus,
    ShareLink,
    Snapshot,
    TryOnSession,
)
from app.services import brand_modules as modules_svc
from app.schemas.share_links import (
    ShareLinkCreate,
    ShareLinkOut,
    ShareMockupPublic,
    SharePublicOut,
    ShareStartRequest,
    ShareUnlockRequest,
)
from app.schemas.sessions import SessionStartResponse
from app.services import prompt_templates
from app.services import r2 as r2_service
from app.services.redis_client import get_redis, ratelimit_key
from app.services.session_limits import max_seconds_for_channel, wallet_channel_for_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["share-links"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mint_token() -> str:
    return secrets.token_urlsafe(24)


def _public_mockups(db: DbSession, mockup_ids: list[UUID], brand_id: UUID) -> list[ShareMockupPublic]:
    rows = db.scalars(
        select(Mockup).where(Mockup.brand_id == brand_id, Mockup.id.in_(mockup_ids))
    ).all()
    by_id = {m.id: m for m in rows}
    out: list[ShareMockupPublic] = []
    for mid in mockup_ids:
        m = by_id.get(mid)
        if m is None:
            continue
        out.append(
            ShareMockupPublic(
                id=m.id,
                name=m.name,
                category=m.category.value if hasattr(m.category, "value") else str(m.category),
                image_url=r2_service.resolve_image_url(m.image_r2_key) or m.image_url,
                version_label=m.version_label,
                prompt_template_key=m.prompt_template_key,
            )
        )
    return out


def _serialize_link(db: DbSession, row: ShareLink) -> ShareLinkOut:
    return ShareLinkOut(
        id=row.id,
        brand_id=row.brand_id,
        token=row.token,
        url_path=f"/s/{row.token}",
        mockup_ids=list(row.mockup_ids),
        has_password=bool(row.password_hash),
        expires_at=row.expires_at,
        max_sessions=row.max_sessions,
        sessions_used=row.sessions_used,
        created_at=row.created_at,
        mockups=_public_mockups(db, list(row.mockup_ids), row.brand_id),
    )


def _assert_link_usable(row: ShareLink) -> None:
    now = _now()
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now >= exp:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Share link expired")
    if row.max_sessions is not None and row.sessions_used >= row.max_sessions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Share link session cap reached",
        )


def _get_by_token(db: DbSession, token: str) -> ShareLink:
    row = db.scalar(select(ShareLink).where(ShareLink.token == token))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")
    return row


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    import hashlib

    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


@router.post("/api/share-links", response_model=ShareLinkOut)
def create_share_link(
    body: ShareLinkCreate,
    current: RequireDesignerPlus,
    db: DbSession,
) -> ShareLinkOut:
    modules_svc.require_module(db, current.brand_id, BrandModuleKind.studio)
    expires = body.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expires_at harus di masa depan",
        )

    mockups = db.scalars(
        select(Mockup).where(
            Mockup.brand_id == current.brand_id,
            Mockup.id.in_(body.mockup_ids),
        )
    ).all()
    if len(mockups) != len(body.mockup_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Satu atau lebih mockup tidak ditemukan di brand ini",
        )

    row = ShareLink(
        brand_id=current.brand_id,
        token=_mint_token(),
        mockup_ids=list(body.mockup_ids),
        password_hash=hash_password(body.password) if body.password else None,
        expires_at=expires,
        max_sessions=body.max_sessions,
        sessions_used=0,
        created_by=current.user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "share_link_create id=%s brand_id=%s mockups=%s",
        row.id,
        row.brand_id,
        [str(x) for x in row.mockup_ids],
    )
    return _serialize_link(db, row)


@router.get("/api/share-links", response_model=list[ShareLinkOut])
def list_share_links(
    current: RequireDesignerPlus,
    db: DbSession,
) -> list[ShareLinkOut]:
    rows = db.scalars(
        select(ShareLink)
        .where(ShareLink.brand_id == current.brand_id)
        .order_by(ShareLink.created_at.desc())
        .limit(50)
    ).all()
    return [_serialize_link(db, r) for r in rows]


def _share_access_ok(row: ShareLink, access_token: str | None) -> bool:
    if not access_token:
        return False
    try:
        payload = decode_share_access_token(access_token)
    except InvalidTokenError:
        return False
    return (
        payload.get("share_token") == row.token
        and payload.get("share_link_id") == str(row.id)
    )


def _public_share_payload(
    db: DbSession,
    row: ShareLink,
    *,
    unlocked: bool,
    access_token: str | None = None,
) -> SharePublicOut:
    brand = db.get(Brand, row.brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")
    requires = bool(row.password_hash)
    remaining = None
    if row.max_sessions is not None:
        remaining = max(0, row.max_sessions - row.sessions_used)
    return SharePublicOut(
        token=row.token,
        brand_name=brand.name,
        requires_password=requires,
        expires_at=row.expires_at,
        max_sessions=row.max_sessions,
        sessions_used=row.sessions_used,
        remaining_sessions=remaining,
        unlocked=unlocked,
        mockups=_public_mockups(db, list(row.mockup_ids), row.brand_id) if unlocked else [],
        tryon_mode=(get_settings().tryon_mode or "mock").lower(),
        access_token=access_token if unlocked else None,
    )


@router.get("/api/public/share/{token}/mockups/{mockup_id}/file")
def public_share_mockup_file(
    token: str,
    mockup_id: UUID,
    request: Request,
    db: DbSession,
    access_token: str | None = None,
):
    """Stream mockup via API (hindari CORS R2 di browser untuk Decart setImage)."""
    from fastapi.responses import Response

    row = _get_by_token(db, token)
    _assert_link_usable(row)
    header_access = request.headers.get("X-Share-Access")
    access = access_token or header_access
    if row.password_hash and not _share_access_ok(row, access):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password required")
    if mockup_id not in list(row.mockup_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mockup not in share")
    mockup = db.scalar(
        select(Mockup).where(Mockup.id == mockup_id, Mockup.brand_id == row.brand_id)
    )
    if mockup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mockup not found")
    try:
        data, content_type = r2_service.get_object_bytes(mockup.image_r2_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("share_mockup_stream_failed id=%s", mockup_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal ambil mockup dari storage",
        ) from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/public/share/{token}", response_model=SharePublicOut)
def get_public_share(
    token: str,
    request: Request,
    db: DbSession,
    access_token: str | None = None,
) -> SharePublicOut:
    """Resolve share tanpa leak brand lain. Mockups hanya jika unlocked / tanpa password."""
    row = _get_by_token(db, token)
    _assert_link_usable(row)

    header_access = request.headers.get("X-Share-Access")
    bearer = None
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()
    access = access_token or header_access or bearer

    requires = bool(row.password_hash)
    if not requires:
        return _public_share_payload(db, row, unlocked=True)

    if _share_access_ok(row, access):
        # Perpanjang/kembalikan token yang sama kalau masih valid
        return _public_share_payload(db, row, unlocked=True, access_token=access)

    return _public_share_payload(db, row, unlocked=False)


@router.post("/api/public/share/{token}/unlock", response_model=SharePublicOut)
def unlock_public_share(
    token: str,
    body: ShareUnlockRequest,
    db: DbSession,
) -> SharePublicOut:
    row = _get_by_token(db, token)
    _assert_link_usable(row)
    if not row.password_hash:
        return _public_share_payload(db, row, unlocked=True)
    if not verify_password(body.password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password salah")
    access = create_share_access_token(
        share_token=row.token,
        share_link_id=row.id,
        expires_at=row.expires_at,
    )
    return _public_share_payload(db, row, unlocked=True, access_token=access)


@router.post("/api/public/share/{token}/sessions/start", response_model=SessionStartResponse)
def start_share_session(
    token: str,
    body: ShareStartRequest,
    request: Request,
    db: DbSession,
) -> SessionStartResponse:
    """Try-on via share: channel=share, debit kuota studio brand (bukan login designer)."""
    settings = get_settings()
    row = _get_by_token(db, token)
    _assert_link_usable(row)

    if row.password_hash:
        pw_ok = bool(body.password) and verify_password(body.password, row.password_hash)
        access_ok = _share_access_ok(row, body.access_token)
        if not pw_ok and not access_ok:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password salah")

    ip_hash = _hash_ip(request.client.host if request.client else None)
    client = get_redis()
    if client is not None and ip_hash:
        key = ratelimit_key(f"share:{ip_hash}")
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        if count > 20:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit")

    mockup_ids = list(row.mockup_ids)
    mockups = db.scalars(
        select(Mockup)
        .options(joinedload(Mockup.collection))
        .where(Mockup.brand_id == row.brand_id, Mockup.id.in_(mockup_ids))
    ).all()
    if not mockups:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mockup not found")

    by_id = {m.id: m for m in mockups}
    active = by_id[mockup_ids[0]]

    wallet_ch = wallet_channel_for_session(SessionChannel.share)
    assert wallet_ch is not None
    wallet = db.scalar(
        select(QuotaWallet).where(
            QuotaWallet.brand_id == row.brand_id,
            QuotaWallet.channel == wallet_ch,
        )
    )
    if wallet is None or wallet.balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Quota exhausted",
        )

    session = TryOnSession(
        brand_id=row.brand_id,
        user_id=None,
        mockup_id=active.id,
        mockup_ids=mockup_ids,
        channel=SessionChannel.share,
        status=SessionStatus.pending,
        device_fingerprint=body.device_fingerprint,
        ip_hash=ip_hash,
        share_link_id=row.id,
    )
    row.sessions_used += 1
    db.add(session)
    db.commit()
    db.refresh(session)

    max_s = max_seconds_for_channel(SessionChannel.share)
    from app.api.routes import sessions as sess
    from app.services.decart import normalized_tryon_mode

    try:
        client_token = sess._mint_client_token(session.id, SessionChannel.share)
    except HTTPException:
        session.status = SessionStatus.failed
        session.error_code = "decart_mint_failed"
        db.commit()
        raise
    image_url = r2_service.resolve_image_url(active.image_r2_key) or active.image_url
    prompt = prompt_templates.get_prompt(active.prompt_template_key)
    from app.schemas.sessions import OutfitItemOut

    outfit = [
        OutfitItemOut(
            mockup_id=m.id,
            name=m.name,
            category=m.category.value if hasattr(m.category, "value") else str(m.category),
            image_url=r2_service.resolve_image_url(m.image_r2_key) or m.image_url,
            prompt_template_key=m.prompt_template_key,
            prompt=prompt_templates.get_prompt(m.prompt_template_key),
        )
        for mid in mockup_ids
        if (m := by_id.get(mid)) is not None
    ]

    logger.info(
        "share_session_start session_id=%s share_link_id=%s brand_id=%s mode=%s",
        session.id,
        row.id,
        row.brand_id,
        normalized_tryon_mode(),
    )

    return SessionStartResponse(
        session_id=session.id,
        status=session.status,
        tryon_mode=normalized_tryon_mode(),
        client_token=client_token,
        mockup_id=active.id,
        mockup_ids=mockup_ids,
        channel=SessionChannel.share,
        prompt_template_key=active.prompt_template_key,
        prompt=prompt,
        image_url=image_url,
        outfit=outfit,
        session_max_seconds=max_s,
        remaining_ms=None,
        ends_at=None,
        debited=False,
        person_detection_hard_gate=settings.person_detection_hard_gate,
    )


def _share_owned_session(db: DbSession, token: str, session_id: UUID) -> TryOnSession:
    link = _get_by_token(db, token)
    row = db.scalar(
        select(TryOnSession).where(
            TryOnSession.id == session_id,
            TryOnSession.brand_id == link.brand_id,
            TryOnSession.share_link_id == link.id,
            TryOnSession.channel == SessionChannel.share,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return row


@router.get("/api/public/share/{token}/sessions/{session_id}")
def public_get_session(token: str, session_id: UUID, db: DbSession):
    from app.api.routes import sessions as sess

    row = _share_owned_session(db, token, session_id)
    if sess._sync_and_maybe_close(row, db=db):
        db.commit()
        db.refresh(row)
    ids = list(row.mockup_ids) if row.mockup_ids else [row.mockup_id]
    outfit = sess._outfit_items(db, ids, row.brand_id)
    return sess._serialize(row, outfit=outfit)


@router.post("/api/public/share/{token}/sessions/{session_id}/active")
def public_mark_active(token: str, session_id: UUID, db: DbSession):
    from app.api.routes import sessions as sess

    row = _share_owned_session(db, token, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if sess._sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )
    now = sess._now()
    if row.started_at is None or row.status == SessionStatus.pending:
        max_s = max_seconds_for_channel(row.channel)
        row.started_at = now
        row.remaining_ms = max_s * 1000
        row.remaining_updated_at = now
        row.duration_active_ms = 0
    elif row.status == SessionStatus.paused:
        row.remaining_updated_at = now
        row.resume_count += 1
    row.status = SessionStatus.active
    sess._debit_once(db, row)
    db.commit()
    db.refresh(row)
    return sess._serialize(row)


@router.post("/api/public/share/{token}/sessions/{session_id}/pause")
def public_pause(token: str, session_id: UUID, db: DbSession):
    from app.api.routes import sessions as sess

    row = _share_owned_session(db, token, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if row.status == SessionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot pause before first active",
        )
    if sess._sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )
    if row.status != SessionStatus.paused:
        left = sess._freeze_active_clock(row)
        row.status = SessionStatus.paused
        row.pause_count += 1
        db.commit()
        db.refresh(row)
        logger.info("share_pause session_id=%s remaining_ms=%s", row.id, left)
    return sess._serialize(row)


@router.post("/api/public/share/{token}/sessions/{session_id}/resume")
def public_resume(token: str, session_id: UUID, db: DbSession):
    from app.api.routes import sessions as sess

    row = _share_owned_session(db, token, session_id)
    if row.status not in (SessionStatus.paused, SessionStatus.active):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session not resumable")
    if sess._sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )
    now = sess._now()
    if row.status == SessionStatus.paused:
        if row.remaining_ms is not None and row.remaining_ms <= 0:
            sess._close_session(row, "timeout", now, db=db)
            db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session closed: timeout")
        row.status = SessionStatus.active
        row.remaining_updated_at = now
        row.resume_count += 1
        db.commit()
        db.refresh(row)
    token_out = sess._mint_client_token(row.id, row.channel)
    return sess._serialize(row, client_token=token_out)


@router.post("/api/public/share/{token}/sessions/{session_id}/client-token")
def public_remint_client_token(token: str, session_id: UUID, db: DbSession):
    from app.api.routes import sessions as sess
    from app.schemas.sessions import ClientTokenOut
    from app.services.decart import normalized_tryon_mode

    row = _share_owned_session(db, token, session_id)
    if sess._sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )
    if row.status not in (
        SessionStatus.pending,
        SessionStatus.active,
        SessionStatus.paused,
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session not open")
    return ClientTokenOut(
        client_token=sess._mint_client_token(row.id, row.channel),
        tryon_mode=normalized_tryon_mode(),
        expires_hint_seconds=300,
    )


@router.post("/api/public/share/{token}/sessions/{session_id}/garment")
def public_garment(token: str, session_id: UUID, body: dict, db: DbSession):
    from app.api.routes import sessions as sess
    from app.schemas.sessions import SessionGarmentRequest

    parsed = SessionGarmentRequest.model_validate(body)
    row = _share_owned_session(db, token, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    allowed = list(row.mockup_ids) if row.mockup_ids else [row.mockup_id]
    if parsed.mockup_id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mockup_id tidak ada di outfit sesi ini",
        )
    row.mockup_id = parsed.mockup_id
    db.commit()
    db.refresh(row)
    outfit = sess._outfit_items(db, allowed, row.brand_id)
    return sess._serialize(row, outfit=outfit)


@router.post("/api/public/share/{token}/sessions/{session_id}/end")
def public_end(token: str, session_id: UUID, body: dict, db: DbSession):
    from app.api.routes import sessions as sess
    from app.schemas.sessions import SessionEndRequest

    parsed = SessionEndRequest.model_validate(body or {})
    if parsed.reason not in sess.ALLOWED_END_REASONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid end reason")
    row = _share_owned_session(db, token, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        return sess._serialize(row)
    sess._close_session(row, parsed.reason, db=db)
    db.commit()
    db.refresh(row)
    return sess._serialize(row)


@router.post("/api/public/share/{token}/sessions/{session_id}/snapshots")
async def public_snapshot(
    token: str,
    session_id: UUID,
    db: DbSession,
    file: UploadFile = File(...),
):
    from app.api.routes import snapshots as snap

    if not r2_service.r2_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="R2 not configured",
        )
    session = _share_owned_session(db, token, session_id)
    data, content_type, ext = await snap._read_image(file)
    row = snap.create_snapshot_row(
        db, session=session, data=data, content_type=content_type, ext=ext
    )
    item = snap._serialize(row)
    return item.model_copy(
        update={
            "content_path": (
                f"/api/public/share/{token}/sessions/{session_id}"
                f"/snapshots/{row.id}/file"
            )
        }
    )


@router.get("/api/public/share/{token}/sessions/{session_id}/snapshots")
def public_list_snapshots(token: str, session_id: UUID, db: DbSession):
    from app.api.routes import snapshots as snap

    _share_owned_session(db, token, session_id)
    link = _get_by_token(db, token)
    rows = db.scalars(
        select(Snapshot)
        .where(
            Snapshot.session_id == session_id,
            Snapshot.brand_id == link.brand_id,
        )
        .order_by(Snapshot.created_at.desc())
        .limit(50)
    ).all()
    out = []
    for r in rows:
        item = snap._serialize(r)
        out.append(
            item.model_copy(
                update={
                    "content_path": (
                        f"/api/public/share/{token}/sessions/{session_id}"
                        f"/snapshots/{r.id}/file"
                    )
                }
            )
        )
    return out


@router.get("/api/public/share/{token}/sessions/{session_id}/snapshots/{snapshot_id}/file")
def public_snapshot_file(
    token: str,
    session_id: UUID,
    snapshot_id: UUID,
    db: DbSession,
):
    from app.api.routes import snapshots as snap

    link = _get_by_token(db, token)
    _share_owned_session(db, token, session_id)
    row = snap.get_owned_snapshot(
        db, session_id=session_id, snapshot_id=snapshot_id, brand_id=link.brand_id
    )
    return snap.stream_snapshot_file(row)


@router.delete(
    "/api/public/share/{token}/sessions/{session_id}/snapshots/{snapshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def public_delete_snapshot(
    token: str,
    session_id: UUID,
    snapshot_id: UUID,
    db: DbSession,
) -> None:
    from app.api.routes import snapshots as snap

    link = _get_by_token(db, token)
    _share_owned_session(db, token, session_id)
    row = snap.get_owned_snapshot(
        db, session_id=session_id, snapshot_id=snapshot_id, brand_id=link.brand_id
    )
    snap.delete_snapshot_row(db, row)
