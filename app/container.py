from dataclasses import dataclass

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.admission import AdmissionGate
from app.config import Settings
from app.db import build_engines, build_sessionmaker
from app.seat_locks import SeatLockCache


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    read_engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]  # writes (and reads inside write transactions)
    read_sessionmaker: async_sessionmaker[AsyncSession]  # read-only queries
    redis: Redis
    seat_locks: SeatLockCache
    admission: AdmissionGate

    async def close(self) -> None:
        await self.engine.dispose()
        if self.read_engine is not self.engine:
            await self.read_engine.dispose()
        await self.redis.aclose()


def build_container(settings: Settings, *, redis: Redis | None = None) -> Container:
    engine, read_engine = build_engines(settings)
    redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
    return Container(
        settings=settings,
        engine=engine,
        read_engine=read_engine,
        sessionmaker=build_sessionmaker(engine),
        read_sessionmaker=build_sessionmaker(read_engine),
        redis=redis,
        seat_locks=SeatLockCache(redis),
        admission=AdmissionGate(settings.max_concurrent_hold_txns, settings.admission_timeout_seconds),
    )


def get_container(request: Request) -> Container:
    return request.app.state.container
