import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, RequireDesignerPlus, require_brand_module
from app.core.config import get_settings
from app.db.models import (
    BrandModuleKind,
    Mockup,
    QuotaWallet,
    SessionChannel,
    SessionStatus,
    TryOnSession,
)
from app.schemas.sessions import (
    ClientTokenOut,
    OutfitItemOut,
    SessionEndRequest,
    SessionGarmentRequest,
    SessionOut,
    SessionStartRequest,
    SessionStartResponse,
)
from app.services import prompt_templates
from app.services import r2 as r2_service
from app.services.decart import mint_client_token, normalized_tryon_mode
from app.services.redis_client import get_redis, ratelimit_key
from app.services.session_limits import max_seconds_for_channel, wallet_channel_for_session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sessions",
    tags=["sessions"],
    dependencies=[Depends(require_brand_module(BrandModuleKind.studio))],
)

ALLOWED_END_REASONS = {"timeout", "abandoned", "error"}
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW_SEC = 60


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


def _mint_client_token(session_id: UUID, channel: SessionChannel | None = None) -> str:
    max_s = max_seconds_for_channel(channel) if channel is not None else None
    return mint_client_token(session_id, max_session_duration=max_s)


# Alias lama (fitting/share import)
_mint_mock_client_token = _mint_client_token


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _live_remaining_ms(row: TryOnSession, now: datetime | None = None) -> int | None:
    """Sisa budget: beku jika paused/pending; berkurang hanya saat active."""
    if row.remaining_ms is None:
        return None
    now = now or _now()
    if row.status != SessionStatus.active or row.remaining_updated_at is None:
        return max(0, row.remaining_ms)
    elapsed = int((now - row.remaining_updated_at).total_seconds() * 1000)
    return max(0, row.remaining_ms - elapsed)


def _freeze_active_clock(row: TryOnSession, now: datetime | None = None) -> int:
    """Persist sisa saat keluar dari active. Return remaining setelah freeze."""
    now = now or _now()
    left = _live_remaining_ms(row, now)
    if left is None:
        return 0
    if row.status == SessionStatus.active and row.remaining_updated_at is not None:
        elapsed = int((now - row.remaining_updated_at).total_seconds() * 1000)
        elapsed = max(0, elapsed)
        prev = row.duration_active_ms or 0
        row.duration_active_ms = prev + elapsed
    row.remaining_ms = left
    row.remaining_updated_at = now
    return left


def _close_session(
    row: TryOnSession,
    reason: str,
    now: datetime | None = None,
    *,
    db: DbSession | None = None,
) -> None:
    now = now or _now()
    if row.status == SessionStatus.active:
        _freeze_active_clock(row, now)
    row.status = SessionStatus.ended
    row.ended_at = now
    row.error_code = reason[:80]
    if row.started_at is not None:
        row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
    # Auto-purge snapshot R2 saat sesi habis (bukan aksi user)
    if db is not None:
        from app.api.routes.snapshots import purge_session_snapshots

        purge_session_snapshots(db, row.id)
        if row.visit_id is not None:
            from app.api.routes.fitting import mark_visit_consumed_for_session

            mark_visit_consumed_for_session(db, row)


def _sync_and_maybe_close(row: TryOnSession, db: DbSession | None = None) -> bool:
    """Timeout / abandoned lazy check. True jika sesi baru saja ditutup."""
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        return False

    settings = get_settings()
    now = _now()

    if row.status == SessionStatus.active:
        left = _live_remaining_ms(row, now)
        if left is not None and left <= 0:
            _close_session(row, "timeout", now, db=db)
            return True

    if row.status == SessionStatus.paused and row.remaining_updated_at is not None:
        pause_age = (now - row.remaining_updated_at).total_seconds()
        if pause_age > settings.session_pause_max_seconds:
            _close_session(row, "abandoned", now, db=db)
            return True

    return False


def _outfit_items(db: DbSession, mockup_ids: list[UUID], brand_id: UUID) -> list[OutfitItemOut]:
    if not mockup_ids:
        return []
    rows = db.scalars(
        select(Mockup).where(Mockup.brand_id == brand_id, Mockup.id.in_(mockup_ids))
    ).all()
    by_id = {m.id: m for m in rows}
    items: list[OutfitItemOut] = []
    for mid in mockup_ids:
        m = by_id.get(mid)
        if m is None:
            continue
        image_url = r2_service.resolve_image_url(m.image_r2_key) or m.image_url
        items.append(
            OutfitItemOut(
                mockup_id=m.id,
                name=m.name,
                category=m.category.value if hasattr(m.category, "value") else str(m.category),
                image_url=image_url,
                prompt_template_key=m.prompt_template_key,
                prompt=prompt_templates.get_prompt(m.prompt_template_key),
            )
        )
    return items


def _serialize(
    row: TryOnSession,
    *,
    client_token: str | None = None,
    outfit: list[OutfitItemOut] | None = None,
) -> SessionOut:
    settings = get_settings()
    now = _now()
    remaining = _live_remaining_ms(row, now)
    ends_hint = None
    if remaining is not None and row.status == SessionStatus.active:
        ends_hint = now + timedelta(milliseconds=remaining)
    active_id = row.mockup_id or row.etalase_product_id
    if active_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session missing garment id",
        )
    ids = list(row.mockup_ids) if row.mockup_ids else [active_id]
    return SessionOut(
        id=row.id,
        brand_id=row.brand_id,
        mockup_id=active_id,
        mockup_ids=ids,
        channel=row.channel,
        status=row.status,
        started_at=row.started_at,
        ended_at=row.ended_at,
        remaining_ms=remaining,
        remaining_updated_at=row.remaining_updated_at,
        duration_active_ms=row.duration_active_ms,
        ends_at=ends_hint or row.ends_at,
        duration_ms=row.duration_ms,
        debited=row.debited,
        error_code=row.error_code,
        pause_count=row.pause_count,
        resume_count=row.resume_count,
        client_token=client_token,
        session_max_seconds=max_seconds_for_channel(row.channel),
        person_detection_hard_gate=settings.person_detection_hard_gate,
        outfit=outfit,
    )


def _get_owned(db: DbSession, session_id: UUID, brand_id: UUID) -> TryOnSession:
    row = db.scalar(
        select(TryOnSession).where(
            TryOnSession.id == session_id,
            TryOnSession.brand_id == brand_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return row


def _rate_limit_start(ip_hash: str | None) -> None:
    if not ip_hash:
        return
    client = get_redis()
    if client is None:
        return
    key = ratelimit_key(ip_hash)
    count = client.incr(key)
    if count == 1:
        client.expire(key, RATE_LIMIT_WINDOW_SEC)
    if count > RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit: too many session starts",
        )


def _reject(
    db: DbSession,
    *,
    brand_id: UUID,
    user_id: UUID,
    mockup_id: UUID,
    mockup_ids: list[UUID] | None,
    channel: SessionChannel,
    error_code: str,
    device_fingerprint: str | None,
    ip_hash: str | None,
    http_status: int,
    detail: str,
) -> None:
    rejected = TryOnSession(
        brand_id=brand_id,
        user_id=user_id,
        mockup_id=mockup_id,
        mockup_ids=mockup_ids or [mockup_id],
        channel=channel,
        status=SessionStatus.rejected,
        error_code=error_code[:80],
        device_fingerprint=device_fingerprint,
        ip_hash=ip_hash,
    )
    db.add(rejected)
    db.commit()
    raise HTTPException(status_code=http_status, detail=detail)


def _debit_once(db: DbSession, row: TryOnSession) -> None:
    if row.debited:
        return
    wallet_ch = wallet_channel_for_session(row.channel)
    if wallet_ch is None:
        row.debited = True
        return

    wallet = db.scalar(
        select(QuotaWallet).where(
            QuotaWallet.brand_id == row.brand_id,
            QuotaWallet.channel == wallet_ch,
        )
    )
    if wallet is None or wallet.balance <= 0:
        channel_label = (
            "Fitting" if wallet_ch and wallet_ch.value == "fitting_room" else "Studio"
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Credit {channel_label} habis",
        )
    wallet.balance -= 1
    row.debited = True
    logger.info(
        "session_debit session_id=%s brand_id=%s channel=%s balance=%s",
        row.id,
        row.brand_id,
        row.channel.value,
        wallet.balance,
    )


def _assert_channel_gate(
    db: DbSession,
    body: SessionStartRequest,
    *,
    brand_id: UUID,
    user_id: UUID,
    mockup_id: UUID,
    mockup_ids: list[UUID],
    ip_hash: str | None,
) -> None:
    ch = body.channel

    if ch in (SessionChannel.fitting_room, SessionChannel.qr):
        _reject(
            db,
            brand_id=brand_id,
            user_id=user_id,
            mockup_id=mockup_id,
            mockup_ids=mockup_ids,
            channel=ch,
            error_code="fitting_visit_required",
            device_fingerprint=body.device_fingerprint,
            ip_hash=ip_hash,
            http_status=status.HTTP_403_FORBIDDEN,
            detail=(
                "Fitting room belum tersedia tanpa visit staff + wishlist code "
                "(task-07). Cooldown global tidak dipakai."
            ),
        )

    if ch == SessionChannel.ecommerce:
        _reject(
            db,
            brand_id=brand_id,
            user_id=user_id,
            mockup_id=mockup_id,
            mockup_ids=mockup_ids,
            channel=ch,
            error_code="ecommerce_not_available",
            device_fingerprint=body.device_fingerprint,
            ip_hash=ip_hash,
            http_status=status.HTTP_403_FORBIDDEN,
            detail=(
                "E-commerce try: 1 try total / shopper / brand belum diaktifkan (V2). "
                "Jika sudah pernah try, start produk lain akan ditolak."
            ),
        )


def _find_open_studio_session(
    db: DbSession, *, brand_id: UUID, user_id: UUID
) -> TryOnSession | None:
    """Satu sesi studio open per user — cegah double-debit (audit-05)."""
    rows = db.scalars(
        select(TryOnSession)
        .where(
            TryOnSession.brand_id == brand_id,
            TryOnSession.user_id == user_id,
            TryOnSession.channel.in_([SessionChannel.studio, SessionChannel.share]),
            TryOnSession.status.in_(
                [SessionStatus.pending, SessionStatus.active, SessionStatus.paused]
            ),
        )
        .order_by(TryOnSession.created_at.desc())
    ).all()
    for row in rows:
        if _sync_and_maybe_close(row, db=db):
            continue
        return row
    return None


@router.get("/open", response_model=SessionOut | None)
def get_open_session(
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut | None:
    """Sesi studio open (pending/active/paused) milik user, atau null."""
    row = _find_open_studio_session(
        db, brand_id=current.brand_id, user_id=current.user.id
    )
    if row is None:
        db.commit()  # persist lazy timeout/abandoned closes
        return None
    db.commit()
    ids = list(row.mockup_ids) if row.mockup_ids else [row.mockup_id]
    outfit = _outfit_items(db, ids, current.brand_id)
    return _serialize(row, outfit=outfit)


@router.post("/start", response_model=SessionStartResponse)
def start_session(
    body: SessionStartRequest,
    request: Request,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionStartResponse:
    settings = get_settings()
    mode = normalized_tryon_mode()

    ip_hash = _hash_ip(request.client.host if request.client else None)
    _rate_limit_start(ip_hash)

    # Gate: jangan buka sesi baru kalau masih ada yang hidup
    open_row = _find_open_studio_session(
        db, brand_id=current.brand_id, user_id=current.user.id
    )
    if open_row is not None:
        db.commit()
        open_ids = list(open_row.mockup_ids) if open_row.mockup_ids else [open_row.mockup_id]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "session_already_open",
                "message": (
                    "Sesi sebelumnya masih aktif — lanjutkan sisa waktu "
                    "sebelum memulai outfit baru."
                ),
                "session_id": str(open_row.id),
                "status": open_row.status.value,
                "remaining_ms": _live_remaining_ms(open_row),
                "mockup_ids": [str(x) for x in open_ids],
            },
        )

    mockup_ids = list(body.mockup_ids or [])
    mockups = db.scalars(
        select(Mockup)
        .options(joinedload(Mockup.collection))
        .where(Mockup.brand_id == current.brand_id, Mockup.id.in_(mockup_ids))
    ).all()
    found = {m.id for m in mockups}
    missing = [mid for mid in mockup_ids if mid not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mockup not found: {missing[0]}",
        )

    active_id = body.mockup_id or mockup_ids[0]
    active = next(m for m in mockups if m.id == active_id)

    _assert_channel_gate(
        db,
        body,
        brand_id=current.brand_id,
        user_id=current.user.id,
        mockup_id=active.id,
        mockup_ids=mockup_ids,
        ip_hash=ip_hash,
    )

    wallet_ch = wallet_channel_for_session(body.channel)
    if wallet_ch is not None:
        wallet = db.scalar(
            select(QuotaWallet).where(
                QuotaWallet.brand_id == current.brand_id,
                QuotaWallet.channel == wallet_ch,
            )
        )
        if wallet is None or wallet.balance <= 0:
            _reject(
                db,
                brand_id=current.brand_id,
                user_id=current.user.id,
                mockup_id=active.id,
                mockup_ids=mockup_ids,
                channel=body.channel,
                error_code="quota_exhausted",
                device_fingerprint=body.device_fingerprint,
                ip_hash=ip_hash,
                http_status=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Credit Studio habis",
            )

    session = TryOnSession(
        brand_id=current.brand_id,
        user_id=current.user.id,
        mockup_id=active.id,
        mockup_ids=mockup_ids,
        channel=body.channel,
        status=SessionStatus.pending,
        device_fingerprint=body.device_fingerprint,
        ip_hash=ip_hash,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    max_s = max_seconds_for_channel(body.channel)
    try:
        client_token = _mint_client_token(session.id, body.channel)
    except HTTPException:
        session.status = SessionStatus.failed
        session.error_code = "decart_mint_failed"
        db.commit()
        raise
    image_url = r2_service.resolve_image_url(active.image_r2_key) or active.image_url
    prompt = prompt_templates.get_prompt(active.prompt_template_key)
    outfit = _outfit_items(db, mockup_ids, current.brand_id)

    logger.info(
        "session_start session_id=%s brand_id=%s channel=%s mockup_ids=%s max_seconds=%s mode=%s",
        session.id,
        session.brand_id,
        body.channel.value,
        [str(x) for x in mockup_ids],
        max_s,
        mode,
    )

    return SessionStartResponse(
        session_id=session.id,
        status=session.status,
        tryon_mode=mode,
        client_token=client_token,
        mockup_id=active.id,
        mockup_ids=mockup_ids,
        channel=body.channel,
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


@router.post("/{session_id}/active", response_model=SessionOut)
def mark_active(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut:
    row = _get_owned(db, session_id, current.brand_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")

    if _sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )

    now = _now()
    first_active = row.started_at is None

    if first_active:
        max_s = max_seconds_for_channel(row.channel)
        row.started_at = now
        row.remaining_ms = max_s * 1000
        row.remaining_updated_at = now
        row.duration_active_ms = 0
        # legacy hint only
        row.ends_at = now + timedelta(seconds=max_s)
    elif row.status == SessionStatus.paused:
        # resume via /active juga OK — restart clock dari sisa beku
        row.remaining_updated_at = now
        row.resume_count += 1
    elif row.status == SessionStatus.active:
        # idempotent re-active: jangan reset remaining
        pass
    elif row.status == SessionStatus.pending:
        max_s = max_seconds_for_channel(row.channel)
        row.started_at = now
        row.remaining_ms = max_s * 1000
        row.remaining_updated_at = now
        row.duration_active_ms = 0
        row.ends_at = now + timedelta(seconds=max_s)

    row.status = SessionStatus.active
    _debit_once(db, row)
    db.commit()
    db.refresh(row)
    logger.info(
        "session_active session_id=%s brand_id=%s remaining_ms=%s debited=%s",
        row.id,
        row.brand_id,
        row.remaining_ms,
        row.debited,
    )
    return _serialize(row)


@router.post("/{session_id}/pause", response_model=SessionOut)
def pause_session(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut:
    row = _get_owned(db, session_id, current.brand_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if row.status == SessionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot pause before first active",
        )

    if _sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )

    if row.status != SessionStatus.paused:
        left = _freeze_active_clock(row)
        row.status = SessionStatus.paused
        row.pause_count += 1
        db.commit()
        db.refresh(row)
        logger.info(
            "session_pause session_id=%s brand_id=%s remaining_ms=%s pause_count=%s",
            row.id,
            row.brand_id,
            left,
            row.pause_count,
        )
    return _serialize(row)


@router.post("/{session_id}/resume", response_model=SessionOut)
def resume_session(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut:
    row = _get_owned(db, session_id, current.brand_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if row.status not in (SessionStatus.paused, SessionStatus.active):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session not resumable",
        )

    if _sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )

    now = _now()
    if row.status == SessionStatus.paused:
        if row.remaining_ms is not None and row.remaining_ms <= 0:
            _close_session(row, "timeout", now, db=db)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Session closed: timeout",
            )
        row.status = SessionStatus.active
        row.remaining_updated_at = now
        row.resume_count += 1
        db.commit()
        db.refresh(row)
        logger.info(
            "session_resume session_id=%s brand_id=%s remaining_ms=%s resume_count=%s",
            row.id,
            row.brand_id,
            row.remaining_ms,
            row.resume_count,
        )

    token = _mint_client_token(row.id, row.channel)
    return _serialize(row, client_token=token)


@router.post("/{session_id}/garment", response_model=SessionOut)
def switch_garment(
    session_id: UUID,
    body: SessionGarmentRequest,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut:
    """Ganti item aktif dalam outfit — tanpa debit / tanpa sesi baru."""
    row = _get_owned(db, session_id, current.brand_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")

    if _sync_and_maybe_close(row, db=db):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )

    allowed = list(row.mockup_ids) if row.mockup_ids else [row.mockup_id]
    if body.mockup_id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mockup_id tidak ada di outfit sesi ini",
        )

    mockup = db.scalar(
        select(Mockup).where(Mockup.id == body.mockup_id, Mockup.brand_id == current.brand_id)
    )
    if mockup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mockup not found")

    row.mockup_id = body.mockup_id
    db.commit()
    db.refresh(row)
    outfit = _outfit_items(db, allowed, current.brand_id)
    logger.info(
        "session_garment session_id=%s mockup_id=%s",
        row.id,
        body.mockup_id,
    )
    return _serialize(row, outfit=outfit)


@router.post("/{session_id}/end", response_model=SessionOut)
def end_session(
    session_id: UUID,
    body: SessionEndRequest,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut:
    if body.reason not in ALLOWED_END_REASONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid end reason (pakai timeout|abandoned|error)",
        )

    row = _get_owned(db, session_id, current.brand_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        return _serialize(row)

    _close_session(row, body.reason, db=db)
    db.commit()
    db.refresh(row)

    logger.info(
        "session_end session_id=%s brand_id=%s channel=%s reason=%s remaining_ms=%s debited=%s",
        row.id,
        row.brand_id,
        row.channel.value,
        body.reason,
        row.remaining_ms,
        row.debited,
    )
    return _serialize(row)


@router.post("/{session_id}/client-token", response_model=ClientTokenOut)
def remint_client_token(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> ClientTokenOut:
    """Mint ulang client token (live reconnect). Gate sesi harus masih open."""
    row = _get_owned(db, session_id, current.brand_id)
    if _sync_and_maybe_close(row, db=db):
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
    token = _mint_client_token(row.id, row.channel)
    return ClientTokenOut(
        client_token=token,
        tryon_mode=normalized_tryon_mode(),
        expires_hint_seconds=300,
    )


@router.get("/{session_id}", response_model=SessionOut)
def get_session(
    session_id: UUID,
    current: RequireDesignerPlus,
    db: DbSession,
) -> SessionOut:
    row = _get_owned(db, session_id, current.brand_id)
    if _sync_and_maybe_close(row, db=db):
        db.commit()
        db.refresh(row)
    ids = list(row.mockup_ids) if row.mockup_ids else [row.mockup_id]
    outfit = _outfit_items(db, ids, current.brand_id)
    return _serialize(row, outfit=outfit)
