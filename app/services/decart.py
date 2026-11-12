"""Decart client-token mint — permanent key hanya di server.

TRYON_MODE=mock → token palsu `mock.{session_id}.*`
TRYON_MODE=live → POST https://api.decart.ai/v1/client/tokens
"""

from __future__ import annotations

import logging
import secrets
from uuid import UUID

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DECART_TOKEN_URL = "https://api.decart.ai/v1/client/tokens"
DECART_CREDITS_URL = "https://api.decart.ai/v1/credits"
LUCY_VTON_MODEL = "lucy-vton-latest"


def normalized_tryon_mode() -> str:
    mode = (get_settings().tryon_mode or "mock").lower().strip()
    return "live" if mode == "live" else "mock"


def mint_mock_client_token(session_id: UUID) -> str:
    return f"mock.{session_id}.{secrets.token_urlsafe(16)}"


def fetch_decart_credits() -> dict:
    """GET /v1/credits — permanent key only (client token → 403).

    Returns {credits: int, key_quota?: {...}}.
    """
    settings = get_settings()
    api_key = (settings.decart_api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DECART_API_KEY belum diisi di server",
        )
    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            res = client.get(
                DECART_CREDITS_URL,
                headers={"x-api-key": api_key},
            )
    except httpx.HTTPError as exc:
        logger.exception("decart_credits_network err=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal hubungi Decart credits",
        ) from exc

    if res.status_code >= 400:
        logger.error(
            "decart_credits_fail status=%s body=%s",
            res.status_code,
            res.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Decart credits gagal ({res.status_code})",
        )

    data = res.json()
    credits = data.get("credits")
    if not isinstance(credits, int):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Decart credits response tidak valid",
        )
    out: dict = {"credits": credits}
    if isinstance(data.get("key_quota"), dict):
        out["key_quota"] = data["key_quota"]
    return out



def mint_client_token(
    session_id: UUID,
    *,
    max_session_duration: int | None = None,
) -> str:
    """Mint setelah gate/kuota OK. Jangan panggil sebelum reject."""
    mode = normalized_tryon_mode()
    if mode == "mock":
        return mint_mock_client_token(session_id)
    return _mint_decart_client_token(
        session_id,
        max_session_duration=max_session_duration,
    )


def _mint_decart_client_token(
    session_id: UUID,
    *,
    max_session_duration: int | None = None,
) -> str:
    settings = get_settings()
    api_key = (settings.decart_api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TRYON_MODE=live membutuhkan DECART_API_KEY di server",
        )

    expires_in = 300  # window Mulai → connect
    duration = max_session_duration or settings.session_max_seconds_studio
    duration = max(10, min(int(duration), 3600))

    body: dict = {
        "expiresIn": expires_in,
        "allowedModels": [LUCY_VTON_MODEL, "lucy-vton-3.6"],
        "constraints": {"realtime": {"maxSessionDuration": duration}},
        "metadata": {"session_id": str(session_id)},
    }
    origins = [
        o.strip()
        for o in (settings.cors_origins or "").split(",")
        if o.strip() and o.strip().startswith(("http://", "https://"))
    ]
    if origins:
        body["allowedOrigins"] = origins[:20]

    try:
        # trust_env=False: jangan ikut HTTP(S)_PROXY lokal yang sering 403 ke Decart
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            res = client.post(
                DECART_TOKEN_URL,
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        logger.exception("decart_mint_network session_id=%s err=%s", session_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal hubungi Decart untuk mint client token",
        ) from exc

    if res.status_code >= 400:
        logger.error(
            "decart_mint_fail session_id=%s status=%s body=%s",
            session_id,
            res.status_code,
            res.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Decart mint gagal ({res.status_code})",
        )

    data = res.json()
    token = data.get("apiKey") or data.get("api_key")
    if not token or not isinstance(token, str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Decart mint response tanpa apiKey",
        )

    logger.info(
        "decart_mint_ok session_id=%s expires_at=%s",
        session_id,
        data.get("expiresAt") or data.get("expires_at"),
    )
    # Jangan log token
    return token
