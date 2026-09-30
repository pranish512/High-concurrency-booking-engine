"""Redis fast path for seat holds.

Redis is a contention filter, not the source of truth: it rejects the losers of a
seat rush in ~1 ms without touching MySQL, and its key TTL mirrors the hold
deadline. If Redis is unavailable or loses keys, requests fall through to the
guarded UPDATE on show_seats, which alone guarantees no double booking.

Keys use a {show_id} hash tag so a multi-seat script stays in one Redis Cluster slot.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

# KEYS[1] = booked-seat set of the show, KEYS[2..] = per-seat hold keys
# ARGV[1] = hold token, ARGV[2] = ttl ms, ARGV[3..] = seat ids (parallel to KEYS[2..])
# All-or-nothing: returns the first conflicting seat id, or nil after taking every seat.
_ACQUIRE = """
for i = 2, #KEYS do
  local seat = ARGV[i + 1]
  if redis.call('SISMEMBER', KEYS[1], seat) == 1 or redis.call('EXISTS', KEYS[i]) == 1 then
    return seat
  end
end
for i = 2, #KEYS do
  redis.call('SET', KEYS[i], ARGV[1], 'PX', ARGV[2])
end
return false
"""

# Compare-and-delete: only the token owner may release its keys.
_RELEASE = """
local released = 0
for i = 1, #KEYS do
  if redis.call('GET', KEYS[i]) == ARGV[1] then
    redis.call('DEL', KEYS[i])
    released = released + 1
  end
end
return released
"""

BOOKED_SET_TTL_SECONDS = 8 * 24 * 3600


def hold_key(show_id: int, seat_id: int) -> str:
    return f"seat:hold:{{{show_id}}}:{seat_id}"


def booked_key(show_id: int) -> str:
    return f"seat:booked:{{{show_id}}}"


class SeatLockCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._acquire = redis.register_script(_ACQUIRE)
        self._release = redis.register_script(_RELEASE)

    async def preload(self) -> None:
        """SCRIPT LOAD both scripts at startup, so a burst never starts with EVALSHA misses."""
        try:
            for script in (self._acquire, self._release):
                script.sha = await self._redis.script_load(script.script)
        except RedisError:
            log.warning("could not preload seat scripts; they will load on first use", exc_info=True)

    async def try_acquire(self, show_id: int, seat_ids: list[int], token: str, ttl_ms: int) -> int | None:
        """Returns a conflicting seat id, or None if all seats were taken (or Redis is down)."""
        keys = [booked_key(show_id), *(hold_key(show_id, s) for s in seat_ids)]
        try:
            conflict = await self._acquire(keys=keys, args=[token, ttl_ms, *seat_ids])
        except RedisError:
            log.warning("redis unavailable for seat fast path; falling back to database", exc_info=True)
            return None
        return int(conflict) if conflict is not None else None

    async def release(self, show_id: int, seat_ids: list[int], token: str) -> None:
        try:
            await self._release(keys=[hold_key(show_id, s) for s in seat_ids], args=[token])
        except RedisError:
            log.warning("could not release hold keys; they will expire on their own", exc_info=True)

    async def forget(self, show_id: int, seat_ids: list[int]) -> None:
        """Drop hold keys after the database released the seats early (cancel / failure)."""
        try:
            await self._redis.delete(*(hold_key(show_id, s) for s in seat_ids))
        except RedisError:
            log.warning("could not drop hold keys; they will expire on their own", exc_info=True)

    async def mark_booked(self, show_id: int, seat_ids: list[int]) -> None:
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.sadd(booked_key(show_id), *seat_ids)
                pipe.expire(booked_key(show_id), BOOKED_SET_TTL_SECONDS)
                await pipe.execute()
        except RedisError:
            log.warning("could not cache booked seats", exc_info=True)
