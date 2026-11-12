"""Durasi & wallet mapping per channel (blueprint §6)."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.models import QuotaChannel, SessionChannel


def max_seconds_for_channel(
    channel: SessionChannel,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    if channel in (SessionChannel.studio, SessionChannel.share):
        return settings.session_max_seconds_studio or settings.session_max_seconds or 120
    if channel in (SessionChannel.fitting_room, SessionChannel.qr):
        return settings.session_max_seconds_fitting or 300
    if channel == SessionChannel.ecommerce:
        return settings.session_max_seconds_ecom or 60
    return settings.session_max_seconds_studio or 120


def wallet_channel_for_session(channel: SessionChannel) -> QuotaChannel | None:
    """None = tidak debit wallet (e-com entitlement / stub)."""
    if channel in (SessionChannel.studio, SessionChannel.share):
        return QuotaChannel.studio
    if channel in (SessionChannel.fitting_room, SessionChannel.qr):
        return QuotaChannel.fitting_room
    return None
