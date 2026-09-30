import asyncio
import random
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings

# InnoDB errors worth retrying: 1213 = deadlock victim, 1205 = lock wait timeout.
RETRYABLE_MYSQL_ERRORS = {1213, 1205}


def build_engines(settings: Settings) -> tuple[AsyncEngine, AsyncEngine]:
    """Returns (writer, reader) engines.

    SQLite allows one writer at a time, so each process gets a single writer
    connection that opens every transaction with BEGIN IMMEDIATE: writers queue
    in the connection pool (async, cheap) instead of failing with SQLITE_BUSY,
    and the write lock is held from the first read of a transaction, which makes
    each check-then-write atomic. Reads use a separate pool and, thanks to WAL,
    never wait for the writer.

    On MySQL a single pooled engine serves both roles.
    """
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        if url.database:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        writer = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=30)
        reader = create_async_engine(url, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow)
        _configure_sqlite(writer, writer=True)
        _configure_sqlite(reader, writer=False)
        return writer, reader

    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
        # READ COMMITTED: no gap locks on the guarded UPDATEs, and an UPDATE that
        # waited on a row lock re-reads the committed row before applying WHERE.
        isolation_level="READ COMMITTED",
    )
    return engine, engine


def _configure_sqlite(engine: AsyncEngine, *, writer: bool) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None  # the "begin" hook below issues BEGIN itself
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")  # other processes' writers: wait, don't fail
        cursor.execute("PRAGMA synchronous=NORMAL")
        if writer:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def on_begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE" if writer else "BEGIN")


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def is_retryable(exc: DBAPIError) -> bool:
    args = getattr(exc.orig, "args", ())
    if args and args[0] in RETRYABLE_MYSQL_ERRORS:
        return True
    return "database is locked" in str(exc.orig)


async def run_in_transaction[T](
    sessionmaker: async_sessionmaker[AsyncSession],
    work: Callable[[AsyncSession], Awaitable[T]],
    *,
    attempts: int = 3,
) -> T:
    """Run `work` in its own transaction; commit on success, roll back on any error.

    Deadlocks, lock-wait timeouts and SQLite busy errors are retried with
    jittered backoff; every other error (including domain errors raised by
    `work`) propagates.
    """
    for attempt in range(1, attempts + 1):
        try:
            async with sessionmaker() as session, session.begin():
                return await work(session)
        except DBAPIError as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            await asyncio.sleep(random.uniform(0, 0.02 * 2**attempt))
    raise AssertionError("unreachable")
