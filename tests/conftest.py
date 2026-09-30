"""Tests build a fresh SQLite database from the real migration (sql/sqlite/migrations)
and use fakeredis in place of a Redis server, so they run with no infrastructure.

They exercise the real service code paths: Lua scripts, BEGIN IMMEDIATE writer,
guarded UPDATEs, idempotency ledger, sweeper and stream consumer.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import fakeredis
import httpx
import pytest
from sqlalchemy import update

from app.config import Settings
from app.container import Container, build_container
from app.main import create_app
from app.migrate import apply_migrations
from app.models import (
    Booking,
    City,
    Language,
    Movie,
    Screen,
    Seat,
    SeatType,
    Show,
    ShowFormat,
    ShowSeat,
    ShowSeatPrice,
    Theatre,
    User,
)
from app.services.bookings import expire_due_holds
from app.services.payments import sign
from app.workers import consume_webhooks_once, ensure_consumer_group

WEBHOOK_SECRET = "test-secret"


@dataclass
class World:
    theatre_id: int
    show_id: int
    later_show_id: int
    show_date: date
    seat_ids: list[int]
    user_ids: list[int]


@pytest.fixture
async def ctx(tmp_path):
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)  # schema only, no seed
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        run_workers=False,
        webhook_secret=WEBHOOK_SECRET,
        max_concurrent_hold_txns=5,
    )
    container = build_container(settings, redis=fakeredis.FakeAsyncRedis(decode_responses=True))
    await ensure_consumer_group(container)
    yield container
    await container.close()


@pytest.fixture
async def world(ctx: Container) -> World:
    tomorrow = date.today() + timedelta(days=1)
    async with ctx.sessionmaker() as s, s.begin():
        # Foreign keys are enforced and these models declare no relationships, so
        # flush parents before children.
        s.add(City(city_id=1, name="Mumbai", state="Maharashtra"))
        s.add(Language(language_id=1, name="Hindi"))
        s.add(Movie(movie_id=1, title="Skyline Heist", duration_min=148, certification="UA", release_date=tomorrow))
        await s.flush()
        s.add(Theatre(theatre_id=1, city_id=1, name="Cineplex Lower Parel", address="Lower Parel"))
        await s.flush()
        s.add(Screen(screen_id=1, theatre_id=1, name="Audi 1"))
        await s.flush()

        seats = [
            Seat(screen_id=1, row_label=row, seat_number=n, seat_type=SeatType.PREMIUM if row == "C" else SeatType.REGULAR)
            for row in "ABC"
            for n in range(1, 11)
        ]
        users = [User(full_name=f"User {i}", email=f"u{i}@example.com", phone=f"90000{i:05d}") for i in range(1, 101)]
        s.add_all(seats + users)
        await s.flush()

        shows = []
        for at in (time(18, 0), time(21, 30)):
            start = datetime.combine(tomorrow, at)
            show = Show(screen_id=1, movie_id=1, language_id=1, format=ShowFormat.TWO_D,
                        start_time=start, end_time=start + timedelta(minutes=168))
            s.add(show)
            await s.flush()
            s.add(ShowSeatPrice(show_id=show.show_id, seat_type=SeatType.REGULAR, price=Decimal("200.00")))
            s.add(ShowSeatPrice(show_id=show.show_id, seat_type=SeatType.PREMIUM, price=Decimal("320.00")))
            s.add_all(ShowSeat(show_id=show.show_id, seat_id=seat.seat_id) for seat in seats)
            shows.append(show.show_id)

    return World(
        theatre_id=1,
        show_id=shows[0],
        later_show_id=shows[1],
        show_date=tomorrow,
        seat_ids=[seat.seat_id for seat in seats],
        user_ids=[u.user_id for u in users],
    )


@pytest.fixture
async def client(ctx: Container):
    app = create_app(container=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---- helpers ---------------------------------------------------------------

async def hold(client, show_id, user_id, seat_ids, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return await client.post(f"/shows/{show_id}/holds", json={"user_id": user_id, "seat_ids": seat_ids}, headers=headers)


async def post_webhook(client, event: dict, secret: str = WEBHOOK_SECRET):
    import json

    body = json.dumps(event).encode()
    return await client.post(
        "/payments/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sign(secret, body)},
    )


async def drain_webhooks(ctx: Container) -> int:
    return await consume_webhooks_once(ctx, "test-consumer", block_ms=None)


async def force_expire(ctx: Container, booking_id: int, *, sweep: bool = True) -> None:
    async with ctx.sessionmaker() as s, s.begin():
        await s.execute(
            update(Booking)
            .where(Booking.booking_id == booking_id)
            .values(expires_at=datetime(2000, 1, 1))
        )
    if sweep:
        await expire_due_holds(ctx)
