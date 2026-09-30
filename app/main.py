import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.container import Container, build_container
from app.errors import DomainError
from app.routers import bookings, catalog, payments
from app.workers import run_hold_sweeper, run_webhook_consumer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx = container or build_container(settings or Settings())
        app.state.container = ctx
        await ctx.seat_locks.preload()
        stop = asyncio.Event()
        tasks = []
        if ctx.settings.run_workers:
            consumer = f"{socket.gethostname()}-{os.getpid()}"
            tasks = [
                asyncio.create_task(run_hold_sweeper(ctx, stop)),
                asyncio.create_task(run_webhook_consumer(ctx, consumer, stop)),
            ]
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if container is None:
                await ctx.close()

    app = FastAPI(
        title="High-Concurrency Booking Engine",
        description="BookMyShow-style ticketing: seat holds, timed expiry, idempotent payment webhooks.",
        version="1.0.0",
        lifespan=lifespan,
    )
    if container is not None:
        app.state.container = container  # available even when the lifespan is not run (tests)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        headers = {"Retry-After": str(exc.details["retry_after"])} if "retry_after" in exc.details else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, **exc.details}},
            headers=headers,
        )

    @app.get("/health", tags=["ops"])
    async def health():
        return {"status": "ok"}

    app.include_router(catalog.router)
    app.include_router(bookings.router)
    app.include_router(payments.router)
    return app


app = create_app()
