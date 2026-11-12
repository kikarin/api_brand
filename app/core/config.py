from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://user:pass@localhost:5432/brand_tryon"
    redis_url: str = "redis://localhost:6379/0"
    tryon_mode: str = "mock"
    decart_api_key: str = ""

    # Per-channel budget aktif (blueprint §6 / revisi 10b)
    session_max_seconds_studio: int = 120
    session_max_seconds_fitting: int = 300
    session_max_seconds_ecom: int = 60
    # Legacy alias — hanya untuk studio jika kode lama masih baca
    session_max_seconds: int = 120

    # Paused lebih lama dari ini → ended reason=abandoned
    session_pause_max_seconds: int = 86400
    # Studio: Mulai tidak menunggu MediaPipe
    person_detection_hard_gate: bool = False

    # Deprecated — cooldown global dihapus (jangan enforce)
    session_cooldown_seconds: int = 0

    qr_rotate_seconds: int = 60
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "brand-mockups"
    r2_public_base_url: str = ""
    # Base publik untuk /local-media (contoh: http://103.31.204.10). Kosong = path relatif.
    public_api_base_url: str = ""
    cf_images_account_hash: str = ""
    secret_key: str = "change-me"
    # studio-web :3000 · fitting-web :3001 · ecom-web :3002
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002"
    environment: str = "development"
    # Self-serve register dimatikan — brand/owner dibuat platform (script/superadmin)
    register_enabled: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
