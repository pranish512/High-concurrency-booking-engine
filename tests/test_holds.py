import asyncio

from sqlalchemy import select

from app.models import Booking, BookingStatus, SeatStatus, ShowSeat
from app.seat_locks import hold_key
from tests.conftest import force_expire, hold


async def seat_status(ctx, show_id, seat_id):
    async with ctx.sessionmaker() as s:
        row = await s.get(ShowSeat, (show_id, seat_id))
        return row.status, row.booking_id


async def test_hold_marks_seats_held_and_prices_them(client, ctx, world):
    a1, c1 = world.seat_ids[0], world.seat_ids[20]
    resp = await hold(client, world.show_id, world.user_ids[0], [c1, a1])

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PENDING"
    assert {s["seat_id"] for s in body["seats"]} == {a1, c1}
    assert body["total_amount"] == "520.00"  # 200 regular + 320 premium
    assert await seat_status(ctx, world.show_id, a1) == (SeatStatus.HELD, body["booking_id"])

    seat_map = (await client.get(f"/shows/{world.show_id}/seats")).json()
    held = {s["seat_id"] for s in seat_map["seats"] if s["status"] == "HELD"}
    assert held == {a1, c1}


async def test_seat_rush_exactly_one_winner(client, ctx, world):
    """100 users hit the same seat at once: one hold, 99 conflicts, no errors."""
    seat = world.seat_ids[5]
    responses = await asyncio.gather(*(hold(client, world.show_id, uid, [seat]) for uid in world.user_ids))

    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1
    assert codes.count(409) == 99

    async with ctx.sessionmaker() as s:
        bookings = (await s.execute(select(Booking).where(Booking.show_id == world.show_id))).scalars().all()
    assert len(bookings) == 1  # losers never reached the database


async def test_overlapping_requests_are_all_or_nothing(client, ctx, world):
    a1, a2, a3 = world.seat_ids[:3]
    first, second = await asyncio.gather(
        hold(client, world.show_id, world.user_ids[0], [a1, a2]),
        hold(client, world.show_id, world.user_ids[1], [a2, a3]),
    )
    winner, loser = (first, second) if first.status_code == 201 else (second, first)
    assert (winner.status_code, loser.status_code) == (201, 409)

    # The loser must not keep a partial hold on its non-conflicting seat.
    free_seat = a3 if winner is first else a1
    assert (await hold(client, world.show_id, world.user_ids[2], [free_seat])).status_code == 201


async def test_database_is_the_backstop_when_redis_forgets(client, ctx, world):
    seat = world.seat_ids[7]
    assert (await hold(client, world.show_id, world.user_ids[0], [seat])).status_code == 201

    await ctx.redis.flushall()  # simulate Redis restart / eviction

    resp = await hold(client, world.show_id, world.user_ids[1], [seat])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "seats_unavailable"
    # ...and the loser's fast-path key was released, not leaked.
    assert await ctx.redis.exists(hold_key(world.show_id, seat)) == 0


async def test_same_seat_in_different_shows_is_independent(client, world):
    seat = world.seat_ids[0]
    assert (await hold(client, world.show_id, world.user_ids[0], [seat])).status_code == 201
    assert (await hold(client, world.later_show_id, world.user_ids[1], [seat])).status_code == 201


async def test_idempotency_key_makes_retries_safe(client, ctx, world):
    seats = world.seat_ids[10:12]
    first = await hold(client, world.show_id, world.user_ids[0], seats, key="retry-123")
    retry = await hold(client, world.show_id, world.user_ids[0], seats, key="retry-123")

    assert first.status_code == retry.status_code == 201
    assert first.json()["booking_id"] == retry.json()["booking_id"]


async def test_sweeper_expires_holds_and_frees_seats(client, ctx, world):
    seat = world.seat_ids[3]
    booking = (await hold(client, world.show_id, world.user_ids[0], [seat])).json()

    await force_expire(ctx, booking["booking_id"])

    assert (await client.get(f"/bookings/{booking['booking_id']}")).json()["status"] == "EXPIRED"
    assert await seat_status(ctx, world.show_id, seat) == (SeatStatus.AVAILABLE, None)
    await ctx.redis.flushall()  # Redis TTL would have lapsed by now in real time
    assert (await hold(client, world.show_id, world.user_ids[1], [seat])).status_code == 201


async def test_lapsed_hold_is_reclaimed_without_waiting_for_sweeper(client, ctx, world):
    seat = world.seat_ids[4]
    booking = (await hold(client, world.show_id, world.user_ids[0], [seat])).json()
    await force_expire(ctx, booking["booking_id"], sweep=False)
    await ctx.redis.flushall()

    resp = await hold(client, world.show_id, world.user_ids[1], [seat])

    assert resp.status_code == 201
    async with ctx.sessionmaker() as s:
        old = await s.get(Booking, booking["booking_id"])
    assert old.status == BookingStatus.EXPIRED


async def test_cancel_releases_immediately(client, ctx, world):
    seat = world.seat_ids[8]
    booking = (await hold(client, world.show_id, world.user_ids[0], [seat])).json()

    resp = await client.delete(f"/bookings/{booking['booking_id']}/hold", params={"user_id": world.user_ids[0]})

    assert resp.json()["status"] == "CANCELLED"
    assert (await hold(client, world.show_id, world.user_ids[1], [seat])).status_code == 201


async def test_rejects_seats_from_another_screen_and_too_many_seats(client, ctx, world):
    resp = await hold(client, world.show_id, world.user_ids[0], [999_999])
    assert resp.status_code == 404
    assert await ctx.redis.exists(hold_key(world.show_id, 999_999)) == 0

    resp = await hold(client, world.show_id, world.user_ids[0], world.seat_ids[:11])
    assert resp.status_code == 422


async def test_admission_gate_sheds_load_with_503(client, ctx, world):
    ctx.admission._timeout = 0.05
    for _ in range(ctx.settings.max_concurrent_hold_txns):
        await ctx.admission._slots.acquire()  # saturate the gate

    resp = await hold(client, world.show_id, world.user_ids[0], [world.seat_ids[9]])

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "1"
    # the fast-path key was released, so a retry after the burst succeeds
    for _ in range(ctx.settings.max_concurrent_hold_txns):
        ctx.admission._slots.release()
    assert (await hold(client, world.show_id, world.user_ids[0], [world.seat_ids[9]])).status_code == 201
