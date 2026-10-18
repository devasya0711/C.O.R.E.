"""Shared async Redis client for rate limiting, caching, and progress events."""

from typing import cast

from redis.asyncio import Redis

BLAST_RADIUS_TTL_SECONDS = 300


def create_redis_client(url: str) -> Redis:
    """Create a lazy pooled connection; application lifespan owns its closure."""
    return cast(
        Redis,
        Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        ),
    )
