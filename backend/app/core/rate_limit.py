"""Atomic per-user Redis sliding windows enforced after JWT verification."""

from collections.abc import Awaitable
from typing import Annotated, cast
from uuid import uuid4

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.auth import get_current_user
from app.core.errors import ApiError
from app.db.models import User

# Redis TIME is authoritative across API processes. Removal, counting, insertion,
# and expiry are one operation; failed requests cannot race past the threshold.
SLIDING_WINDOW_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry = math.max(1, math.ceil((tonumber(first[2]) + window - now) / 1000))
    return {0, retry}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window + 1000)
return {1, limit - count - 1}
"""


def rate_bucket(method: str, path: str) -> tuple[str, int]:
    """Apply the exact read/graph and ingestion/migration budgets in API.md."""
    if (
        method == "POST" and path.rstrip("/") == "/api/v1/repos"
    ) or "/migrations" in path:
        return "jobs", 10
    return "graph", 60


async def enforce_rate_limit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Fail closed on Redis outage and never trust an unverified user header."""
    client = cast(Redis | None, request.app.state.redis)
    if client is None:
        raise ApiError(
            503, "RATE_LIMITER_UNAVAILABLE", "Request limiting is unavailable."
        )
    bucket, limit = rate_bucket(request.method, request.url.path)
    key = f"core:rate:{user.id}:{bucket}"
    try:
        result = await cast(
            Awaitable[object],
            client.eval(
                SLIDING_WINDOW_SCRIPT, 1, key, "60000", str(limit), str(uuid4())
            ),
        )
    except RedisError as error:
        raise ApiError(
            503, "RATE_LIMITER_UNAVAILABLE", "Request limiting is unavailable."
        ) from error
    if not isinstance(result, list) or len(result) != 2:
        raise ApiError(
            503, "RATE_LIMITER_UNAVAILABLE", "Request limiting is unavailable."
        )
    if int(result[0]) == 0:
        raise ApiError(
            429,
            "RATE_LIMIT_EXCEEDED",
            "Too many requests. Retry after the indicated interval.",
            headers={"Retry-After": str(int(result[1]))},
        )
