from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api.routes import auth as auth_routes
from app.api.routes import brand_modules as brand_modules_routes
from app.api.routes import brand_users as brand_users_routes
from app.api.routes import platform as platform_routes
from app.api.routes import studio_users as studio_users_routes
from app.api.routes import collections as collections_routes
from app.api.routes import etalase as etalase_routes
from app.api.routes import fitting as fitting_routes
from app.api.routes import mockups as mockups_routes
from app.api.routes import sessions as sessions_routes
from app.api.routes import share_links as share_links_routes
from app.api.routes import snapshots as snapshots_routes
from app.core.config import get_settings
from app.services import r2 as r2_service

settings = get_settings()

app = FastAPI(
    title="Platform API",
    description="Satu backend untuk Mockup Studio, Fitting Store, dan E-com Try On.",
    version="0.1.0",
    docs_url="/docs" if settings.environment != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(brand_modules_routes.router)
app.include_router(brand_users_routes.router)
app.include_router(studio_users_routes.router)
app.include_router(platform_routes.router)
app.include_router(collections_routes.router)
app.include_router(mockups_routes.router)
app.include_router(etalase_routes.router)
app.include_router(sessions_routes.router)
app.include_router(share_links_routes.router)
app.include_router(snapshots_routes.router)
app.include_router(fitting_routes.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "tryon_mode": settings.tryon_mode,
    }


@app.get("/local-media/{object_key:path}")
def local_media(object_key: str) -> Response:
    """Dev fallback saat R2 kosong — file dari .local-data/uploads."""
    if ".." in object_key.split("/"):
        raise HTTPException(status_code=400, detail="Invalid key")
    try:
        data, content_type = r2_service.get_object_bytes(object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Not found") from exc
    return Response(content=data, media_type=content_type)
