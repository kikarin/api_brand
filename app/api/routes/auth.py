from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.core.slug import slugify
from app.db.models import Brand, BrandStatus, QuotaChannel, QuotaWallet, User, UserRole
from app.schemas.auth import (
    AuthResponse,
    BrandOut,
    LoginRequest,
    MeResponse,
    QuotaItem,
    QuotaResponse,
    RegisterRequest,
    TryOnStatusOut,
    UserOut,
)

router = APIRouter(prefix="/api", tags=["auth"])

TRIAL_STUDIO_SESSIONS = 20
TRIAL_FITTING_SESSIONS = 10


def _unique_slug(db: DbSession, base: str) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while db.scalar(select(Brand.id).where(Brand.slug == candidate)) is not None:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(request: Request, db: DbSession) -> AuthResponse:
    """Self-serve register — default mati. Brand/owner lewat script/superadmin."""
    # Cek flag dulu supaya selalu 403 (bukan 422 validasi body) saat dimatikan.
    if not get_settings().register_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Registrasi publik dimatikan. "
                "Minta platform membuat brand, atau login dengan akun yang sudah ada."
            ),
        )

    try:
        payload = await request.json()
        body = RegisterRequest.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid register payload",
        ) from exc

    email = body.email.lower().strip()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    now = datetime.now(timezone.utc)
    brand = Brand(
        name=body.brand_name.strip(),
        slug=_unique_slug(db, body.brand_name),
        status=BrandStatus.active,
        plan="starter",
    )
    db.add(brand)
    db.flush()

    user = User(
        brand_id=brand.id,
        email=email,
        password_hash=hash_password(body.password),
        role=UserRole.owner,
    )
    wallet = QuotaWallet(
        brand_id=brand.id,
        channel=QuotaChannel.studio,
        balance=TRIAL_STUDIO_SESSIONS,
        period_start=now,
        period_end=now + timedelta(days=30),
    )
    fitting_wallet = QuotaWallet(
        brand_id=brand.id,
        channel=QuotaChannel.fitting_room,
        balance=TRIAL_FITTING_SESSIONS,
        period_start=now,
        period_end=now + timedelta(days=30),
    )
    db.add(user)
    db.add(wallet)
    db.add(fitting_wallet)
    # Default modul: studio saja. Fitting Store = opt-in owner
    # (POST /api/brand/modules/fitting_store/enable).
    from app.services import brand_modules as modules_svc

    modules_svc.seed_default_studio(db, brand.id)
    db.commit()
    db.refresh(user)
    db.refresh(brand)

    token = create_access_token(
        user_id=user.id,
        brand_id=brand.id,
        role=user.role.value,
        email=user.email,
    )
    return AuthResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        brand=BrandOut.model_validate(brand),
    )


@router.post("/auth/login", response_model=AuthResponse)
def login(body: LoginRequest, db: DbSession) -> AuthResponse:
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    brand = db.get(Brand, user.brand_id)
    if brand is None or brand.status != BrandStatus.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brand inactive")

    token = create_access_token(
        user_id=user.id,
        brand_id=brand.id,
        role=user.role.value,
        email=user.email,
    )
    return AuthResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        brand=BrandOut.model_validate(brand),
    )


@router.post("/auth/logout")
def logout(_: AuthUser) -> dict[str, str]:
    """JWT stateless — client harus hapus token. Endpoint untuk konvensi FE."""
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
def me(current: AuthUser) -> MeResponse:
    return MeResponse(
        user=UserOut.model_validate(current.user),
        brand=BrandOut.model_validate(current.brand),
    )


@router.get("/me/tryon-status", response_model=TryOnStatusOut)
def tryon_status(_: AuthUser) -> TryOnStatusOut:
    """Mode try-on + sisa credit Decart (hanya saat live; key tetap di server)."""
    from app.services.decart import fetch_decart_credits, normalized_tryon_mode

    mode = normalized_tryon_mode()
    if mode != "live":
        return TryOnStatusOut(
            tryon_mode="mock",
            decart_credits=None,
            decart_credits_error=None,
        )
    try:
        data = fetch_decart_credits()
        return TryOnStatusOut(
            tryon_mode="live",
            decart_credits=int(data["credits"]),
            decart_credits_error=None,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Gagal ambil credits"
        return TryOnStatusOut(
            tryon_mode="live",
            decart_credits=None,
            decart_credits_error=detail,
        )


@router.get("/me/quota", response_model=QuotaResponse)
def my_quota(current: AuthUser, db: DbSession) -> QuotaResponse:
    # Lazy-seed fitting_room untuk brand lama (pre task-07)
    fitting = db.scalar(
        select(QuotaWallet).where(
            QuotaWallet.brand_id == current.brand_id,
            QuotaWallet.channel == QuotaChannel.fitting_room,
        )
    )
    if fitting is None:
        now = datetime.now(timezone.utc)
        fitting = QuotaWallet(
            brand_id=current.brand_id,
            channel=QuotaChannel.fitting_room,
            balance=TRIAL_FITTING_SESSIONS,
            period_start=now,
            period_end=now + timedelta(days=30),
        )
        db.add(fitting)
        db.commit()

    wallets = db.scalars(
        select(QuotaWallet).where(QuotaWallet.brand_id == current.brand_id)
    ).all()
    return QuotaResponse(
        brand_id=current.brand_id,
        wallets=[
            QuotaItem(
                channel=w.channel,
                balance=w.balance,
                period_start=w.period_start,
                period_end=w.period_end,
            )
            for w in wallets
        ],
    )
