"""ORM models — brands, users, quota, collections, mockups."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class BrandStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class BrandModuleKind(str, enum.Enum):
    studio = "studio"
    fitting_store = "fitting_store"


class BrandModuleStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class UserRole(str, enum.Enum):
    owner = "owner"
    designer = "designer"
    viewer = "viewer"
    # Toko (task-11) — terpisah dari studio
    store_admin = "store_admin"
    fitting_attendant = "fitting_attendant"


class QuotaChannel(str, enum.Enum):
    studio = "studio"
    fitting_room = "fitting_room"
    store = "store"  # deprecated — jangan pakai; legacy DB


class MockupCategory(str, enum.Enum):
    top = "top"
    bottom = "bottom"
    outer = "outer"
    dress = "dress"  # one-piece: badan sampai kaki
    hat = "hat"
    shoes = "shoes"
    other = "other"


class SessionChannel(str, enum.Enum):
    studio = "studio"
    share = "share"  # ikuti aturan studio
    fitting_room = "fitting_room"
    ecommerce = "ecommerce"
    qr = "qr"  # deprecated alias → treat as fitting_room stub


class SessionStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    paused = "paused"
    ended = "ended"
    failed = "failed"
    rejected = "rejected"


class PlatformAdmin(Base):
    """Superadmin platform — bukan user brand."""

    __tablename__ = "platform_admins"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    status: Mapped[BrandStatus] = mapped_column(
        Enum(BrandStatus, name="brand_status", native_enum=False),
        default=BrandStatus.active,
        nullable=False,
    )
    plan: Mapped[str] = mapped_column(String(40), default="starter", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list[User]] = relationship(back_populates="brand")
    quota_wallets: Mapped[list[QuotaWallet]] = relationship(back_populates="brand")
    collections: Mapped[list[Collection]] = relationship(back_populates="brand")
    mockups: Mapped[list[Mockup]] = relationship(back_populates="brand")
    modules: Mapped[list[BrandModule]] = relationship(back_populates="brand")
    etalase_products: Mapped[list[EtalaseProduct]] = relationship(back_populates="brand")
    branches: Mapped[list[Branch]] = relationship(back_populates="brand")


class BrandModule(Base):
    """Modul langganan per brand — studio dan/atau fitting_store (independen)."""

    __tablename__ = "brand_modules"
    __table_args__ = (
        UniqueConstraint("brand_id", "module", name="uq_brand_modules_brand_module"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[BrandModuleKind] = mapped_column(
        Enum(BrandModuleKind, name="brand_module_kind", native_enum=False),
        nullable=False,
    )
    status: Mapped[BrandModuleStatus] = mapped_column(
        Enum(BrandModuleStatus, name="brand_module_status", native_enum=False),
        default=BrandModuleStatus.active,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    brand: Mapped[Brand] = relationship(back_populates="modules")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False),
        default=UserRole.owner,
        nullable=False,
    )
    # Attendant biasanya terikat cabang; store_admin boleh null (semua cabang)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    brand: Mapped[Brand] = relationship(back_populates="users")
    branch: Mapped[Branch | None] = relationship(back_populates="staff")


class QuotaWallet(Base):
    __tablename__ = "quota_wallets"
    __table_args__ = (
        UniqueConstraint("brand_id", "channel", name="uq_quota_wallets_brand_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[QuotaChannel] = mapped_column(
        Enum(QuotaChannel, name="quota_channel", native_enum=False),
        nullable=False,
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    brand: Mapped[Brand] = relationship(back_populates="quota_wallets")


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    brand: Mapped[Brand] = relationship(back_populates="collections")
    mockups: Mapped[list[Mockup]] = relationship(back_populates="collection")


class Mockup(Base):
    __tablename__ = "mockups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[MockupCategory] = mapped_column(
        Enum(MockupCategory, name="mockup_category", native_enum=False),
        nullable=False,
    )
    image_r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_template_key: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    brand: Mapped[Brand] = relationship(back_populates="mockups")
    collection: Mapped[Collection | None] = relationship(back_populates="mockups")
    # DB FK ondelete=CASCADE — jangan biarkan ORM null-kan mockup_id (NOT NULL)
    sessions: Mapped[list[TryOnSession]] = relationship(
        back_populates="mockup",
        passive_deletes=True,
    )


class EtalaseProduct(Base):
    """Barang toko (Fitting Store) — terpisah total dari mockups studio."""

    __tablename__ = "etalase_products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[MockupCategory] = mapped_column(
        Enum(MockupCategory, name="mockup_category", native_enum=False),
        nullable=False,
    )
    image_r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_template_key: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    brand: Mapped[Brand] = relationship(back_populates="etalase_products")
    branch: Mapped[Branch | None] = relationship(back_populates="etalase_products")
    qr_codes: Mapped[list[EtalaseQrCode]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class EtalaseQrCode(Base):
    """QR permanen per produk etalase (printable) — bukan claim visit stub."""

    __tablename__ = "etalase_qr_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("etalase_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product: Mapped[EtalaseProduct] = relationship(back_populates="qr_codes")


class TryOnSession(Base):
    __tablename__ = "tryon_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mockup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mockups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    etalase_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("etalase_products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mockup_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )  # studio: mockup ids; fitting: etalase product ids
    channel: Mapped[SessionChannel] = mapped_column(
        Enum(SessionChannel, name="session_channel", native_enum=False),
        default=SessionChannel.studio,
        nullable=False,
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status", native_enum=False),
        default=SessionStatus.pending,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Legacy — jangan dipakai sebagai sumber kebenaran timer (revisi 10b)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_active_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    debited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    device_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pause_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resume_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    share_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fitting_visits.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    mockup: Mapped[Mockup] = relationship(back_populates="sessions")
    share_link: Mapped[ShareLink | None] = relationship(back_populates="sessions")
    visit: Mapped[FittingVisit | None] = relationship(
        back_populates="bound_session", foreign_keys=[visit_id]
    )
    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="session")


class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    mockup_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sessions_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list[TryOnSession]] = relationship(back_populates="share_link")


class Branch(Base):
    """Cabang toko — kapasitas AI fitting room (`max_ai_rooms`)."""

    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    max_ai_rooms: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    brand: Mapped[Brand] = relationship(back_populates="branches")
    visits: Mapped[list[FittingVisit]] = relationship(back_populates="branch")
    etalase_products: Mapped[list[EtalaseProduct]] = relationship(back_populates="branch")
    staff: Mapped[list[User]] = relationship(back_populates="branch")


class FittingVisit(Base):
    """Customer wishlist visit — 1 sesi / visit, channel fitting_room."""

    __tablename__ = "fitting_visits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    wishlist_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    item_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )  # etalase_product ids
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    room_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tryon_sessions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_fitting_visits_session_id",
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    branch: Mapped[Branch | None] = relationship(back_populates="visits")
    bound_session: Mapped[TryOnSession | None] = relationship(
        back_populates="visit",
        foreign_keys="TryOnSession.visit_id",
        uselist=False,
    )
    qr_tokens: Mapped[list[QrToken]] = relationship(back_populates="visit")


class QrToken(Base):
    """Claim token sekali pakai (opsional) → visit staff."""

    __tablename__ = "qr_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fitting_visits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tryon_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    visit: Mapped[FittingVisit] = relationship(back_populates="qr_tokens")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tryon_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mockup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mockups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[TryOnSession] = relationship(back_populates="snapshots")
