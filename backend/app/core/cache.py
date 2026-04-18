from collections.abc import AsyncIterator
from typing import cast

from redis.asyncio import Redis

from app.config import get_settings


def get_redis_client() -> Redis:
    settings = get_settings()
    return cast(Redis, Redis.from_url(settings.redis_url, decode_responses=True))


async def redis_client_context() -> AsyncIterator[Redis]:
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.aclose()
