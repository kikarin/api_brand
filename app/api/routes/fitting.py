"""Fitting room — staff issue wishlist + public claim / session (channel=fitting_room).

Policy debit: wallet `fitting_room` berkurang 1× saat pertama `active`
(sama pola studio). Kuota studio tidak ikut. 1 sesi / visit.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from app.api.deps import (
    DbSession,
    RequireFittingStaff,
    RequireStoreAdmin,
)
from app.core.config import get_settings
from app.db.models import (
    Brand,
    Branch,
    EtalaseProduct,
    FittingVisit,
    QuotaChannel,
    QuotaWallet,
    QrToken,
    SessionChannel,
    SessionStatus,
    TryOnSession,
)
from app.schemas.etalase import (
    BranchUpdate,
    PublicEtalaseListItem,
    PublicEtalaseProductOut,
)
from app.schemas.fitting import (
    BranchCreate,
    BranchOut,
    FittingStartRequest,
    FittingStartResponse,
    PublicVisitOut,
    PublicWishlistCreate,
    VisitAssignRequest,
    VisitIssueRequest,
    VisitOut,
)
from app.schemas.sessions import OutfitItemOut, SessionEndRequest, SessionGarmentRequest, SessionOut
from app.services import prompt_templates
from app.services import r2 as r2_service
from app.services.redis_client import get_redis, ratelimit_key
from app.services.session_limits import max_seconds_for_channel, wallet_channel_for_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["fitting"])

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # tanpa 0O1I
TRIAL_FITTING_SESSIONS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_wishlist_code(db: DbSession, length: int = 6) -> str:
    for _ in range(40):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        exists = db.scalar(
            select(FittingVisit.id).where(FittingVisit.wishlist_code == code)
        )
        if exists is None:
            return code
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Gagal generate wishlist code unik",
    )


def _ensure_fitting_wallet(db: DbSession, brand_id: UUID) -> QuotaWallet:
    wallet = db.scalar(
        select(QuotaWallet).where(
            QuotaWallet.brand_id == brand_id,
            QuotaWallet.channel == QuotaChannel.fitting_room,
        )
    )
    if wallet is not None:
        return wallet
    now = _now()
    wallet = QuotaWallet(
        brand_id=brand_id,
        channel=QuotaChannel.fitting_room,
        balance=TRIAL_FITTING_SESSIONS,
        period_start=now,
        period_end=now + timedelta(days=30),
    )
    db.add(wallet)
    db.flush()
    return wallet


def _outfit_for_ids(db: DbSession, brand_id: UUID, item_ids: list[UUID]) -> list[OutfitItemOut]:
    """item_ids = etalase_product ids."""
    if not item_ids:
        return []
    rows = db.scalars(
        select(EtalaseProduct).where(
            EtalaseProduct.brand_id == brand_id,
            EtalaseProduct.id.in_(item_ids),
        )
    ).all()
    by_id = {p.id: p for p in rows}
    out: list[OutfitItemOut] = []
    for pid in item_ids:
        p = by_id.get(pid)
        if p is None:
            continue
        out.append(
            OutfitItemOut(
                mockup_id=p.id,  # field legacy: id garment aktif
                name=p.name,
                category=p.category.value if hasattr(p.category, "value") else str(p.category),
                image_url=r2_service.resolve_image_url(p.image_r2_key) or p.image_url,
                prompt_template_key=p.prompt_template_key,
                prompt=prompt_templates.get_prompt(p.prompt_template_key),
            )
        )
    return out


def _visit_status(
    row: FittingVisit,
    now: datetime | None = None,
    *,
    session_status: str | None = None,
) -> str:
    now = now or _now()
    if row.consumed_at is not None:
        return "consumed"
    if row.session_id is not None:
        if session_status in ("pending", "active", "paused"):
            return "in_session"
        if session_status in ("ended", "failed", "rejected"):
            return "consumed"
        return "in_session"
    if row.expires_at <= now:
        return "expired"
    if row.claimed_at is not None:
        return "claimed"
    if row.room_no is not None and row.assigned_at is not None:
        return "assigned"
    return "open"


def _serialize_visit(
    row: FittingVisit,
    *,
    qr: QrToken | None = None,
    session_status: str | None = None,
) -> VisitOut:
    st = session_status
    if st is None and row.session_id is not None:
        # caller boleh pass; fallback tanpa query
        pass
    return VisitOut(
        id=row.id,
        brand_id=row.brand_id,
        branch_id=row.branch_id,
        wishlist_code=row.wishlist_code,
        item_ids=list(row.item_ids),
        expires_at=row.expires_at,
        claimed_at=row.claimed_at,
        consumed_at=row.consumed_at,
        session_id=row.session_id,
        created_at=row.created_at,
        room_no=row.room_no,
        assigned_at=row.assigned_at,
        claim_path=f"/f/{row.wishlist_code}",
        qr_token=qr.token if qr else None,
        qr_path=f"/q/{qr.token}" if qr else None,
        qr_expires_at=qr.expires_at if qr else None,
        status=_visit_status(row, session_status=session_status),
        session_status=session_status,
    )


def _get_visit_by_code(db: DbSession, code: str) -> FittingVisit:
    row = db.scalar(
        select(FittingVisit).where(FittingVisit.wishlist_code == code.strip().upper())
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code tidak ditemukan")
    return row


def _count_open_fitting_at_branch(db: DbSession, branch_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(TryOnSession)
            .join(FittingVisit, TryOnSession.visit_id == FittingVisit.id)
            .where(
                FittingVisit.branch_id == branch_id,
                TryOnSession.channel == SessionChannel.fitting_room,
                TryOnSession.status.in_(
                    [SessionStatus.pending, SessionStatus.active, SessionStatus.paused]
                ),
            )
        )
        or 0
    )


def _hash_ip(host: str | None) -> str | None:
    if not host:
        return None
    import hashlib

    return hashlib.sha256(host.encode()).hexdigest()[:32]


# ── Staff: branches ──────────────────────────────────────────────


@router.get("/api/branches", response_model=list[BranchOut])
def list_branches(current: RequireFittingStaff, db: DbSession) -> list[BranchOut]:
    from app.db.models import UserRole

    stmt = (
        select(Branch)
        .where(Branch.brand_id == current.brand_id)
        .order_by(Branch.created_at.asc())
    )
    if (
        current.role == UserRole.fitting_attendant
        and current.branch_id is not None
    ):
        stmt = stmt.where(Branch.id == current.branch_id)
    rows = db.scalars(stmt).all()
    return [BranchOut.model_validate(r) for r in rows]


@router.post("/api/branches", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
def create_branch(
    body: BranchCreate, current: RequireStoreAdmin, db: DbSession
) -> BranchOut:
    row = Branch(
        id=uuid.uuid4(),
        brand_id=current.brand_id,
        name=body.name.strip(),
        max_ai_rooms=body.max_ai_rooms,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("branch_create id=%s brand_id=%s name=%s", row.id, row.brand_id, row.name)
    return BranchOut.model_validate(row)


@router.patch("/api/branches/{branch_id}", response_model=BranchOut)
def update_branch(
    branch_id: UUID,
    body: BranchUpdate,
    current: RequireStoreAdmin,
    db: DbSession,
) -> BranchOut:
    """Update nama / max_ai_rooms — UI admin penuh di task-12."""
    row = db.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.brand_id == current.brand_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    if body.name is not None:
        row.name = body.name.strip()
    if body.max_ai_rooms is not None:
        row.max_ai_rooms = body.max_ai_rooms
    db.commit()
    db.refresh(row)
    return BranchOut.model_validate(row)


# ── Staff: issue visit ───────────────────────────────────────────


@router.get("/api/fitting-visits/catalog", response_model=list[OutfitItemOut])
def fitting_wishlist_catalog(
    current: RequireFittingStaff, db: DbSession
) -> list[OutfitItemOut]:
    """Katalog baca-saja dari **etalase** (bukan mockup studio).

    Stub task-07 (staff pilih item → issue code) dimatikan — customer yang
    pilih etalase di task-13. Endpoint ini hanya preview katalog toko.
    """
    from app.db.models import EtalaseProduct

    rows = db.scalars(
        select(EtalaseProduct)
        .where(
            EtalaseProduct.brand_id == current.brand_id,
            EtalaseProduct.is_active.is_(True),
        )
        .order_by(EtalaseProduct.created_at.desc())
        .limit(200)
    ).all()
    return [
        OutfitItemOut(
            # Field name legacy; value = etalase_product.id (bukan mockup)
            mockup_id=p.id,
            name=p.name,
            category=p.category.value if hasattr(p.category, "value") else str(p.category),
            image_url=r2_service.resolve_image_url(p.image_r2_key) or p.image_url,
            prompt_template_key=p.prompt_template_key,
            prompt=prompt_templates.get_prompt(p.prompt_template_key),
        )
        for p in rows
    ]


@router.get("/api/fitting-visits", response_model=list[VisitOut])
def list_visits(current: RequireFittingStaff, db: DbSession) -> list[VisitOut]:
    from app.db.models import UserRole

    stmt = (
        select(FittingVisit)
        .where(FittingVisit.brand_id == current.brand_id)
        .order_by(FittingVisit.created_at.desc())
        .limit(50)
    )
    if (
        current.role == UserRole.fitting_attendant
        and current.branch_id is not None
    ):
        stmt = stmt.where(FittingVisit.branch_id == current.branch_id)
    rows = db.scalars(stmt).all()
    session_ids = [r.session_id for r in rows if r.session_id is not None]
    status_by_id: dict[UUID, str] = {}
    if session_ids:
        sessions = db.scalars(
            select(TryOnSession).where(TryOnSession.id.in_(session_ids))
        ).all()
        status_by_id = {s.id: s.status.value for s in sessions}
    return [
        _serialize_visit(
            r,
            session_status=status_by_id.get(r.session_id) if r.session_id else None,
        )
        for r in rows
    ]


@router.post("/api/fitting-visits/{visit_id}/end", response_model=VisitOut)
def staff_end_visit(
    visit_id: UUID,
    current: RequireFittingStaff,
    db: DbSession,
) -> VisitOut:
    """Staff toko akhiri visit/sesi fitting — consume code, tutup sesi jika masih open."""
    from app.api.routes import sessions as sess
    from app.db.models import UserRole

    visit = db.scalar(
        select(FittingVisit).where(
            FittingVisit.id == visit_id,
            FittingVisit.brand_id == current.brand_id,
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")
    if (
        current.role == UserRole.fitting_attendant
        and current.branch_id is not None
        and visit.branch_id != current.branch_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Attendant hanya boleh akhiri visit di cabangnya",
        )

    session_status: str | None = None
    if visit.session_id is not None:
        row = db.scalar(
            select(TryOnSession).where(
                TryOnSession.id == visit.session_id,
                TryOnSession.brand_id == current.brand_id,
            )
        )
        if row is not None and row.status not in (
            SessionStatus.ended,
            SessionStatus.failed,
            SessionStatus.rejected,
        ):
            sess._close_session(row, "staff_end", db=db)
            session_status = SessionStatus.ended.value
        elif row is not None:
            session_status = row.status.value

    if visit.consumed_at is None:
        visit.consumed_at = _now()
    db.commit()
    db.refresh(visit)

    logger.info(
        "fitting_staff_end visit_id=%s code=%s session_id=%s by=%s",
        visit.id,
        visit.wishlist_code,
        visit.session_id,
        current.user.id,
    )
    return _serialize_visit(visit, session_status=session_status or "ended")


@router.post(
    "/api/fitting-visits",
    response_model=VisitOut,
    status_code=status.HTTP_201_CREATED,
)
def issue_visit(
    body: VisitIssueRequest, current: RequireFittingStaff, db: DbSession
) -> VisitOut:
    # Stub task-07 dimatikan: customer pilih etalase → staff hanya kode+room.
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Staff tidak lagi memilih item. "
            "Customer buat wishlist dari etalase (POST /api/public/brands/{slug}/wishlist)."
        ),
    )


@router.post(
    "/api/fitting-visits/by-code/{code}/assign",
    response_model=VisitOut,
)
def assign_room_by_code(
    code: str,
    body: VisitAssignRequest,
    current: RequireFittingStaff,
    db: DbSession,
) -> VisitOut:
    """Attendant input wishlist code + pilih room."""
    from app.db.models import UserRole

    visit = _get_visit_by_code(db, code)
    if visit.brand_id != current.brand_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code tidak ditemukan")
    if (
        current.role == UserRole.fitting_attendant
        and current.branch_id is not None
        and visit.branch_id is not None
        and visit.branch_id != current.branch_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Attendant hanya boleh assign di cabangnya",
        )

    st = _visit_status(visit)
    if st in ("expired", "consumed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Visit {st} — tidak bisa assign",
        )
    if visit.session_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit sudah punya sesi",
        )

    branch = None
    if visit.branch_id is not None:
        branch = db.get(Branch, visit.branch_id)
    if branch is None:
        branch = db.scalar(
            select(Branch)
            .where(Branch.brand_id == current.brand_id)
            .order_by(Branch.created_at.asc())
            .limit(1)
        )
        if branch is not None and visit.branch_id is None:
            visit.branch_id = branch.id
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Brand belum punya cabang — buat branch dulu",
        )
    if body.room_no < 1 or body.room_no > branch.max_ai_rooms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"room_no harus 1..{branch.max_ai_rooms}",
        )

    # Room sibuk jika ada visit assigned/open session di nomor sama
    busy = db.scalar(
        select(FittingVisit.id).where(
            FittingVisit.brand_id == current.brand_id,
            FittingVisit.branch_id == branch.id,
            FittingVisit.room_no == body.room_no,
            FittingVisit.id != visit.id,
            FittingVisit.consumed_at.is_(None),
            FittingVisit.expires_at > _now(),
            FittingVisit.assigned_at.is_not(None),
        )
    )
    if busy is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ruang {body.room_no} sedang dipakai",
        )

    visit.room_no = body.room_no
    visit.assigned_by_user_id = current.user.id
    visit.assigned_at = _now()
    db.commit()
    db.refresh(visit)
    logger.info(
        "fitting_assign code=%s room=%s by=%s",
        visit.wishlist_code,
        body.room_no,
        current.user.id,
    )
    return _serialize_visit(visit)


@router.get(
    "/api/public/brands/{slug}/etalase",
    response_model=list[PublicEtalaseListItem],
)
def public_list_etalase(slug: str, db: DbSession) -> list[PublicEtalaseListItem]:
    brand = db.scalar(select(Brand).where(Brand.slug == slug.strip().lower()))
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    from app.services import etalase_qr as qr_svc

    rows = db.scalars(
        select(EtalaseProduct)
        .where(
            EtalaseProduct.brand_id == brand.id,
            EtalaseProduct.is_active.is_(True),
        )
        .order_by(EtalaseProduct.created_at.desc())
        .limit(200)
    ).all()
    out: list[PublicEtalaseListItem] = []
    for p in rows:
        qr = qr_svc.ensure_qr(db, p)
        out.append(
            PublicEtalaseListItem(
                product_id=p.id,
                name=p.name,
                sku=p.sku,
                category=p.category.value if hasattr(p.category, "value") else str(p.category),
                image_url=r2_service.resolve_image_url(p.image_r2_key) or p.image_url,
                qr_token=qr.token,
                qr_path=f"/e/{qr.token}",
            )
        )
    db.commit()
    return out


@router.post(
    "/api/public/brands/{slug}/wishlist",
    response_model=PublicVisitOut,
    status_code=status.HTTP_201_CREATED,
)
def public_create_wishlist(
    slug: str,
    body: PublicWishlistCreate,
    db: DbSession,
) -> PublicVisitOut:
    brand = db.scalar(select(Brand).where(Brand.slug == slug.strip().lower()))
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")

    products = db.scalars(
        select(EtalaseProduct).where(
            EtalaseProduct.brand_id == brand.id,
            EtalaseProduct.id.in_(body.product_ids),
            EtalaseProduct.is_active.is_(True),
        )
    ).all()
    by_id = {p.id: p for p in products}
    ordered = [by_id[pid] for pid in body.product_ids if pid in by_id]
    if len(ordered) != len(body.product_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Satu atau lebih product_id tidak valid",
        )

    branch_id = body.branch_id
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if branch is None or branch.brand_id != brand.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="branch_id invalid")
    else:
        first = db.scalar(
            select(Branch)
            .where(Branch.brand_id == brand.id)
            .order_by(Branch.created_at.asc())
            .limit(1)
        )
        branch_id = first.id if first else None

    now = _now()
    visit = FittingVisit(
        brand_id=brand.id,
        branch_id=branch_id,
        wishlist_code=_gen_wishlist_code(db),
        item_ids=[p.id for p in ordered],
        issued_by_user_id=None,
        expires_at=now + timedelta(minutes=body.ttl_minutes),
    )
    db.add(visit)
    db.commit()
    db.refresh(visit)
    logger.info(
        "fitting_wishlist_create code=%s brand=%s items=%s",
        visit.wishlist_code,
        brand.slug,
        len(ordered),
    )
    return _public_visit_payload(db, visit)


# ── Public: resolve / claim ──────────────────────────────────────


def _public_visit_payload(db: DbSession, visit: FittingVisit) -> PublicVisitOut:
    from app.api.routes import sessions as sess

    brand = db.get(Brand, visit.brand_id)
    session_status: str | None = None
    remaining_ms: int | None = None
    remaining_updated_at = None
    mockup_id = None
    if visit.session_id is not None:
        row = db.get(TryOnSession, visit.session_id)
        if row is not None:
            # Sync timeout/abandoned supaya status visit akurat
            if sess._sync_and_maybe_close(row, db=db):
                _mark_visit_consumed(db, row)
                db.commit()
                db.refresh(visit)
            else:
                session_status = row.status.value
                remaining_ms = sess._live_remaining_ms(row)
                remaining_updated_at = row.remaining_updated_at
                mockup_id = row.etalase_product_id or row.mockup_id

    return PublicVisitOut(
        visit_id=visit.id,
        brand_name=brand.name if brand else "Brand",
        wishlist_code=visit.wishlist_code,
        status=_visit_status(visit, session_status=session_status),
        expires_at=visit.expires_at,
        claimed_at=visit.claimed_at,
        consumed_at=visit.consumed_at,
        room_no=visit.room_no,
        assigned_at=visit.assigned_at,
        outfit=_outfit_for_ids(db, visit.brand_id, list(visit.item_ids)),
        session_max_seconds=max_seconds_for_channel(SessionChannel.fitting_room),
        session_id=visit.session_id,
        session_status=session_status,
        remaining_ms=remaining_ms,
        remaining_updated_at=remaining_updated_at,
        mockup_id=mockup_id,
    )


@router.get("/api/public/fitting/{code}/mockups/{mockup_id}/file")
def public_fitting_mockup_file(code: str, mockup_id: UUID, db: DbSession):
    """Stream etalase garment via API — Decart setImage tanpa CORS R2.

    Path param tetap `mockup_id` (legacy); value = etalase_product.id.
    """
    from fastapi.responses import Response

    visit = _get_visit_by_code(db, code)
    if mockup_id not in list(visit.item_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item tidak ada di antrian visit",
        )
    product = db.scalar(
        select(EtalaseProduct).where(
            EtalaseProduct.id == mockup_id,
            EtalaseProduct.brand_id == visit.brand_id,
        )
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    try:
        data, content_type = r2_service.get_object_bytes(product.image_r2_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fitting_product_stream_failed id=%s", mockup_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal ambil gambar dari storage",
        ) from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/public/fitting/{code}", response_model=PublicVisitOut)
def public_get_visit(code: str, db: DbSession) -> PublicVisitOut:
    visit = _get_visit_by_code(db, code)
    return _public_visit_payload(db, visit)


def _claim_visit_row(db: DbSession, visit: FittingVisit) -> FittingVisit:
    now = _now()
    st = _visit_status(visit, now)
    if st == "expired":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Code sudah expired — minta staff issue ulang",
        )
    if st == "consumed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit sudah dipakai — 1 sesi per code",
        )
    if visit.session_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit sudah punya sesi — tidak bisa claim ulang",
        )
    if visit.claimed_at is None:
        visit.claimed_at = now
        db.commit()
        db.refresh(visit)
    return visit


@router.post("/api/public/fitting/{code}/claim", response_model=PublicVisitOut)
def public_claim_visit(code: str, db: DbSession) -> PublicVisitOut:
    visit = _claim_visit_row(db, _get_visit_by_code(db, code))
    return _public_visit_payload(db, visit)


@router.get("/api/public/qr/{token}", response_model=PublicVisitOut)
def public_get_qr(token: str, db: DbSession) -> PublicVisitOut:
    qr = db.scalar(select(QrToken).where(QrToken.token == token))
    if qr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR tidak ditemukan")
    now = _now()
    if qr.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="QR expired — pakai wishlist code atau minta staff issue ulang",
        )
    visit = db.get(FittingVisit, qr.visit_id)
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit tidak ditemukan")
    return _public_visit_payload(db, visit)


@router.post("/api/public/qr/{token}/claim", response_model=PublicVisitOut)
def public_claim_qr(token: str, db: DbSession) -> PublicVisitOut:
    qr = db.scalar(select(QrToken).where(QrToken.token == token))
    if qr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR tidak ditemukan")
    now = _now()
    if qr.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="QR expired")
    if qr.claimed_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="QR sudah di-claim")
    visit = db.get(FittingVisit, qr.visit_id)
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit tidak ditemukan")
    visit = _claim_visit_row(db, visit)
    qr.claimed_at = now
    db.commit()
    return _public_visit_payload(db, visit)


# ── Public: start + lifecycle ────────────────────────────────────


def _fitting_start_response(
    db: DbSession,
    visit: FittingVisit,
    session: TryOnSession,
    settings,
) -> FittingStartResponse:
    from app.api.routes import sessions as sess
    from app.services.decart import normalized_tryon_mode

    active_id = session.etalase_product_id or session.mockup_id
    active = db.get(EtalaseProduct, active_id) if active_id else None
    if active is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Garment not found")
    item_ids = list(session.mockup_ids) if session.mockup_ids else list(visit.item_ids)
    mode = normalized_tryon_mode()
    client_token = sess._mint_client_token(session.id, SessionChannel.fitting_room)
    image_url = r2_service.resolve_image_url(active.image_r2_key) or active.image_url
    prompt = prompt_templates.get_prompt(active.prompt_template_key)
    outfit = _outfit_for_ids(db, visit.brand_id, item_ids)
    remaining = sess._live_remaining_ms(session)

    return FittingStartResponse(
        session_id=session.id,
        status=session.status,
        tryon_mode=mode,
        client_token=client_token,
        mockup_id=active.id,
        mockup_ids=item_ids,
        channel=SessionChannel.fitting_room,
        prompt_template_key=active.prompt_template_key,
        prompt=prompt,
        image_url=image_url,
        outfit=outfit,
        session_max_seconds=max_seconds_for_channel(SessionChannel.fitting_room),
        remaining_ms=remaining,
        ends_at=None,
        debited=session.debited,
        person_detection_hard_gate=settings.person_detection_hard_gate,
        visit_id=visit.id,
        wishlist_code=visit.wishlist_code,
    )


@router.post(
    "/api/public/fitting/{code}/sessions/start",
    response_model=FittingStartResponse,
)
def public_start_fitting(
    code: str,
    body: FittingStartRequest,
    request: Request,
    db: DbSession,
) -> FittingStartResponse:
    from app.api.routes import sessions as sess

    settings = get_settings()
    visit = _get_visit_by_code(db, code)
    now = _now()
    st = _visit_status(visit, now)

    if st == "expired":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Code expired — minta staff issue ulang",
        )
    if st == "consumed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit sudah consumed — sesi kedua ditolak",
        )
    if visit.room_no is None or visit.assigned_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Belum di-assign room — serahkan kode ke attendant dulu",
        )
    if body.room_no is not None and body.room_no != visit.room_no:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Room salah — seharusnya Ruang {visit.room_no}",
        )

    # Idempotent: visit sudah punya sesi open → kembalikan sesi itu (bukan 409).
    # Kasus umum: FE kehilangan sessionId setelah jeda/refresh lalu tekan Mulai lagi.
    if visit.session_id is not None:
        existing = db.get(TryOnSession, visit.session_id)
        if existing is not None:
            if sess._sync_and_maybe_close(existing, db=db):
                _mark_visit_consumed(db, existing)
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Visit sudah consumed — sesi kedua ditolak",
                )
            if existing.status in (
                SessionStatus.pending,
                SessionStatus.active,
                SessionStatus.paused,
            ):
                # Pause dulu jika masih active — FE akan Lanjut / mark-active
                if existing.status == SessionStatus.active:
                    sess._freeze_active_clock(existing)
                    existing.status = SessionStatus.paused
                    existing.pause_count += 1
                    db.commit()
                    db.refresh(existing)
                return _fitting_start_response(db, visit, existing, settings)
        # Sesi aneh/hilang — lepaskan ikatan supaya bisa start ulang
        visit.session_id = None
        db.commit()

    # capacity cabang
    if visit.branch_id is not None:
        branch = db.get(Branch, visit.branch_id)
        if branch is not None:
            open_n = _count_open_fitting_at_branch(db, branch.id)
            if open_n >= branch.max_ai_rooms:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Kapasitas AI room penuh ({branch.max_ai_rooms}). "
                        "Tunggu sesi lain selesai."
                    ),
                )

    wallet = _ensure_fitting_wallet(db, visit.brand_id)
    if wallet.balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Credit Fitting habis",
        )

    ip_hash = _hash_ip(request.client.host if request.client else None)
    client = get_redis()
    if client is not None and ip_hash:
        key = ratelimit_key(f"fitting:{ip_hash}")
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        if count > 20:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit",
            )

    item_ids = list(visit.item_ids)
    active_id = body.mockup_id or item_ids[0]
    if active_id not in item_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="product_id tidak ada di antrian visit",
        )

    products = db.scalars(
        select(EtalaseProduct).where(
            EtalaseProduct.brand_id == visit.brand_id,
            EtalaseProduct.id.in_(item_ids),
        )
    ).all()
    by_id = {p.id: p for p in products}
    active = by_id.get(active_id)
    if active is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if visit.claimed_at is None:
        visit.claimed_at = now

    session = TryOnSession(
        brand_id=visit.brand_id,
        user_id=None,
        mockup_id=None,
        etalase_product_id=active.id,
        mockup_ids=item_ids,
        channel=SessionChannel.fitting_room,
        status=SessionStatus.pending,
        device_fingerprint=body.device_fingerprint,
        ip_hash=ip_hash,
        visit_id=visit.id,
    )
    db.add(session)
    db.flush()
    visit.session_id = session.id
    db.commit()
    db.refresh(session)

    try:
        out = _fitting_start_response(db, visit, session, settings)
    except HTTPException:
        session.status = SessionStatus.failed
        session.error_code = "decart_mint_failed"
        visit.session_id = None
        db.commit()
        raise

    logger.info(
        "fitting_session_start session_id=%s visit_id=%s code=%s brand_id=%s mode=%s",
        session.id,
        visit.id,
        visit.wishlist_code,
        visit.brand_id,
        out.tryon_mode,
    )
    return out


def _fitting_owned_session(db: DbSession, code: str, session_id: UUID) -> TryOnSession:
    visit = _get_visit_by_code(db, code)
    row = db.scalar(
        select(TryOnSession).where(
            TryOnSession.id == session_id,
            TryOnSession.brand_id == visit.brand_id,
            TryOnSession.visit_id == visit.id,
            TryOnSession.channel == SessionChannel.fitting_room,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return row


@router.get("/api/public/fitting/{code}/sessions/{session_id}", response_model=SessionOut)
def public_get_session(code: str, session_id: UUID, db: DbSession) -> SessionOut:
    from app.api.routes import sessions as sess

    row = _fitting_owned_session(db, code, session_id)
    if sess._sync_and_maybe_close(row, db=db):
        _mark_visit_consumed(db, row)
        db.commit()
        db.refresh(row)
    ids = list(row.mockup_ids) if row.mockup_ids else (
        [row.etalase_product_id or row.mockup_id]
        if (row.etalase_product_id or row.mockup_id)
        else []
    )
    outfit = _outfit_for_ids(db, row.brand_id, [i for i in ids if i is not None])
    return sess._serialize(row, outfit=outfit)


@router.post("/api/public/fitting/{code}/sessions/{session_id}/active", response_model=SessionOut)
def public_mark_active(code: str, session_id: UUID, db: DbSession) -> SessionOut:
    from app.api.routes import sessions as sess

    row = _fitting_owned_session(db, code, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if sess._sync_and_maybe_close(row, db=db):
        _mark_visit_consumed(db, row)
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
    # Debit wallet fitting_room (bukan studio)
    assert wallet_channel_for_session(row.channel) == QuotaChannel.fitting_room
    sess._debit_once(db, row)
    db.commit()
    db.refresh(row)
    return sess._serialize(row)


@router.post("/api/public/fitting/{code}/sessions/{session_id}/pause", response_model=SessionOut)
def public_pause(code: str, session_id: UUID, db: DbSession) -> SessionOut:
    from app.api.routes import sessions as sess

    row = _fitting_owned_session(db, code, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if row.status == SessionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot pause before first active",
        )
    if sess._sync_and_maybe_close(row, db=db):
        _mark_visit_consumed(db, row)
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
        logger.info("fitting_pause session_id=%s remaining_ms=%s", row.id, left)
    return sess._serialize(row)


@router.post("/api/public/fitting/{code}/sessions/{session_id}/resume", response_model=SessionOut)
def public_resume(code: str, session_id: UUID, db: DbSession) -> SessionOut:
    from app.api.routes import sessions as sess

    row = _fitting_owned_session(db, code, session_id)
    if row.status not in (SessionStatus.paused, SessionStatus.active):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session not resumable")
    if sess._sync_and_maybe_close(row, db=db):
        _mark_visit_consumed(db, row)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )
    now = sess._now()
    if row.status == SessionStatus.paused:
        if row.remaining_ms is not None and row.remaining_ms <= 0:
            sess._close_session(row, "timeout", now, db=db)
            _mark_visit_consumed(db, row)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Session closed: timeout"
            )
        row.status = SessionStatus.active
        row.remaining_updated_at = now
        row.resume_count += 1
        db.commit()
        db.refresh(row)
    token_out = sess._mint_client_token(row.id, row.channel)
    return sess._serialize(row, client_token=token_out)


@router.post("/api/public/fitting/{code}/sessions/{session_id}/client-token")
def public_remint_client_token(code: str, session_id: UUID, db: DbSession):
    from app.api.routes import sessions as sess
    from app.schemas.sessions import ClientTokenOut
    from app.services.decart import normalized_tryon_mode

    row = _fitting_owned_session(db, code, session_id)
    if sess._sync_and_maybe_close(row, db=db):
        _mark_visit_consumed(db, row)
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


@router.post("/api/public/fitting/{code}/sessions/{session_id}/garment", response_model=SessionOut)
def public_garment(
    code: str, session_id: UUID, body: SessionGarmentRequest, db: DbSession
) -> SessionOut:
    from app.api.routes import sessions as sess

    row = _fitting_owned_session(db, code, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed")
    if sess._sync_and_maybe_close(row, db=db):
        _mark_visit_consumed(db, row)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session closed: {row.error_code}",
        )
    allowed = list(row.mockup_ids) if row.mockup_ids else (
        [row.etalase_product_id] if row.etalase_product_id else []
    )
    if body.mockup_id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="product_id tidak ada di antrian visit",
        )
    row.etalase_product_id = body.mockup_id
    row.mockup_id = None
    db.commit()
    db.refresh(row)
    # Fitting pakai etalase_product ids — jangan _outfit_items (itu tabel Mockup)
    outfit = _outfit_for_ids(db, row.brand_id, list(allowed))
    return sess._serialize(row, outfit=outfit)


@router.post("/api/public/fitting/{code}/sessions/{session_id}/end", response_model=SessionOut)
def public_end(
    code: str, session_id: UUID, body: SessionEndRequest, db: DbSession
) -> SessionOut:
    from app.api.routes import sessions as sess

    if body.reason not in sess.ALLOWED_END_REASONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid end reason")
    row = _fitting_owned_session(db, code, session_id)
    if row.status in (SessionStatus.ended, SessionStatus.failed, SessionStatus.rejected):
        _mark_visit_consumed(db, row)
        db.commit()
        return sess._serialize(row)
    sess._close_session(row, body.reason, db=db)
    _mark_visit_consumed(db, row)
    db.commit()
    db.refresh(row)
    return sess._serialize(row)


def _mark_visit_consumed(db: DbSession, row: TryOnSession) -> None:
    if row.visit_id is None:
        return
    visit = db.get(FittingVisit, row.visit_id)
    if visit is None:
        return
    if visit.consumed_at is None and row.status in (
        SessionStatus.ended,
        SessionStatus.failed,
        SessionStatus.rejected,
    ):
        visit.consumed_at = _now()
        if visit.session_id is None:
            visit.session_id = row.id
        logger.info(
            "fitting_visit_consumed visit_id=%s session_id=%s reason=%s",
            visit.id,
            row.id,
            row.error_code,
        )


def mark_visit_consumed_for_session(db: DbSession, row: TryOnSession) -> None:
    """Dipanggil dari sessions._close_session agar timeout lazy juga consume visit."""
    _mark_visit_consumed(db, row)
