from __future__ import annotations

import logging
from functools import lru_cache

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_redis() -> redis.Redis | None:
    settings = get_settings()
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        logger.warning("Redis unavailable — rate-limit degraded", exc_info=True)
        return None


def ratelimit_key(ip_hash: str) -> str:
    return f"ratelimit:start:{ip_hash}"
