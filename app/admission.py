import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.errors import Overloaded


class AdmissionGate:
    """Caps in-flight hold transactions per process.

    During a burst, requests queue here (bounded wait) instead of piling onto the
    DB connection pool; anything still waiting after `timeout` is shed with 503 so
    clients back off rather than time out.
    """

    def __init__(self, limit: int, timeout: float) -> None:
        self._slots = asyncio.Semaphore(limit)
        self._timeout = timeout

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._slots.acquire(), self._timeout)
        except TimeoutError:
            raise Overloaded("Too many concurrent booking attempts, retry shortly", retry_after=1) from None
        try:
            yield
        finally:
            self._slots.release()
