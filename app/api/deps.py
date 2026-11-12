from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token, decode_platform_access_token
from app.db.models import (
    Brand,
    BrandModuleKind,
    BrandStatus,
    PlatformAdmin,
    User,
    UserRole,
)
from app.db.session import get_db
from app.services import brand_modules as modules_svc

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]

# ── Role groups ──────────────────────────────────────────────────
STUDIO_ROLES = {UserRole.owner, UserRole.designer, UserRole.viewer}
STORE_ADMIN_ROLES = {UserRole.owner, UserRole.store_admin}
FITTING_STAFF_ROLES = {
    UserRole.owner,
    UserRole.store_admin,
    UserRole.fitting_attendant,
}
ETALASE_ADMIN_ROLES = {UserRole.owner, UserRole.store_admin}


class CurrentUser:
    def __init__(self, user: User, brand: Brand):
        self.user = user
        self.brand = brand

    @property
    def brand_id(self) -> UUID:
        return self.user.brand_id

    @property
    def role(self) -> UserRole:
        return self.user.role

    @property
    def branch_id(self) -> UUID | None:
        return self.user.branch_id


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = UUID(payload["sub"])
        brand_id = UUID(payload["brand_id"])
    except (InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from None

    user = db.get(User, user_id)
    if user is None or user.brand_id != brand_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    brand = db.get(Brand, brand_id)
    if brand is None or brand.status != BrandStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Brand inactive or missing",
        )

    return CurrentUser(user=user, brand=brand)


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[AuthUser], CurrentUser]:
    allowed = set(roles)

    def _dependency(current: AuthUser) -> CurrentUser:
        if current.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current.role.value}' not allowed",
            )
        return current

    return _dependency


RequireOwner = Annotated[CurrentUser, Depends(require_roles(UserRole.owner))]
RequireDesignerPlus = Annotated[
    CurrentUser, Depends(require_roles(UserRole.owner, UserRole.designer))
]
# Studio members only — role toko tidak ikut
RequireAnyMember = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.owner, UserRole.designer, UserRole.viewer)),
]


def require_brand_module(module: BrandModuleKind) -> Callable[..., CurrentUser]:
    """Gate route berdasarkan brand_modules aktif."""

    def _dependency(current: AuthUser, db: DbSession) -> CurrentUser:
        modules_svc.require_module(db, current.brand_id, module)
        return current

    return _dependency


RequireStudioModule = Annotated[
    CurrentUser, Depends(require_brand_module(BrandModuleKind.studio))
]
RequireFittingStoreModule = Annotated[
    CurrentUser, Depends(require_brand_module(BrandModuleKind.fitting_store))
]


def _require_roles_and_fitting_module(
    *roles: UserRole,
) -> Callable[..., CurrentUser]:
    allowed = set(roles)

    def _dependency(current: AuthUser, db: DbSession) -> CurrentUser:
        if current.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current.role.value}' not allowed",
            )
        modules_svc.require_module(
            db, current.brand_id, BrandModuleKind.fitting_store
        )
        return current

    return _dependency


# Etalase CRUD / QR print (task-12) — owner + store_admin
RequireEtalaseAdmin = Annotated[
    CurrentUser,
    Depends(
        _require_roles_and_fitting_module(UserRole.owner, UserRole.store_admin)
    ),
]

# Console fitting — owner + store_admin + attendant
RequireFittingStaff = Annotated[
    CurrentUser,
    Depends(
        _require_roles_and_fitting_module(
            UserRole.owner,
            UserRole.store_admin,
            UserRole.fitting_attendant,
        )
    ),
]

# Kelola cabang (create/update capacity) — bukan attendant
RequireStoreAdmin = Annotated[
    CurrentUser,
    Depends(
        _require_roles_and_fitting_module(UserRole.owner, UserRole.store_admin)
    ),
]

# Invite user toko
RequireStoreUserAdmin = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.owner, UserRole.store_admin)),
]


# Alias lama (task-10) → etalase admin
RequireOwnerFittingStore = RequireEtalaseAdmin


class CurrentPlatformAdmin:
    def __init__(self, admin: PlatformAdmin):
        self.admin = admin

    @property
    def id(self) -> UUID:
        return self.admin.id

    @property
    def email(self) -> str:
        return self.admin.email


def get_platform_admin(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentPlatformAdmin:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = decode_platform_access_token(credentials.credentials)
        admin_id = UUID(payload["sub"])
    except (InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired platform token",
        ) from None

    admin = db.get(PlatformAdmin, admin_id)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Platform admin not found",
        )
    return CurrentPlatformAdmin(admin)


RequirePlatformAdmin = Annotated[CurrentPlatformAdmin, Depends(get_platform_admin)]
