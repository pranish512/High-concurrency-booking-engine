"""Seat holds: acquire, release, expire.

Hold path, cheapest check first:
  1. Redis Lua script — all-or-nothing SET NX on every seat. Rush losers stop here.
  2. Admission gate  — bounded number of DB transactions in flight per process.
  3. MySQL           — guarded UPDATE ... WHERE status='AVAILABLE' on show_seats.
                        Affected rows must equal requested seats, or roll back.
Step 3 alone guarantees correctness; 1 and 2 exist to survive the burst.

Lock order everywhere: payments -> bookings -> show_seats (ascending PK),
which keeps the hold, sweeper and webhook paths from deadlocking each other.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import as_utc, utcnow
from app.container import Container
from app.db import run_in_transaction
from app.errors import InvalidRequest, InvalidState, NotFound, SeatsUnavailable
from app.models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Seat,
    SeatStatus,
    Show,
    ShowSeat,
    ShowSeatPrice,
    ShowStatus,
    User,
)
from app.schemas import BookedSeat, BookingOut


@dataclass
class ReleasedHold:
    booking_id: int
    show_id: int
    seat_ids: list[int]


async def create_hold(
    ctx: Container, *, show_id: int, user_id: int, seat_ids: list[int], idempotency_key: str | None
) -> BookingOut:
    seat_ids = sorted(set(seat_ids))
    if len(seat_ids) > ctx.settings.max_seats_per_booking:
        raise InvalidRequest(f"At most {ctx.settings.max_seats_per_booking} seats per booking")

    if idempotency_key:
        existing = await _find_by_idempotency_key(ctx, user_id, idempotency_key)
        if existing is not None:
            return existing

    ttl = ctx.settings.hold_ttl_seconds
    token = uuid.uuid4().hex
    conflict = await ctx.seat_locks.try_acquire(show_id, seat_ids, token, ttl * 1000)
    if conflict is not None:
        raise SeatsUnavailable([conflict])

    try:
        async with ctx.admission.slot():
            booking_id = await run_in_transaction(
                ctx.sessionmaker,
                lambda s: _hold_seats(s, show_id, user_id, seat_ids, idempotency_key, ttl),
            )
    except IntegrityError:
        # Same idempotency key raced in from a retry: return the booking that won.
        await ctx.seat_locks.release(show_id, seat_ids, token)
        if idempotency_key and (existing := await _find_by_idempotency_key(ctx, user_id, idempotency_key)):
            return existing
        raise
    except BaseException:
        await ctx.seat_locks.release(show_id, seat_ids, token)
        raise
    return await get_booking(ctx, booking_id)


async def _hold_seats(
    session: AsyncSession,
    show_id: int,
    user_id: int,
    seat_ids: list[int],
    idempotency_key: str | None,
    ttl_seconds: int,
) -> int:
    now = utcnow()
    show = await session.get(Show, show_id)
    if show is None or show.status != ShowStatus.SCHEDULED:
        raise NotFound("Show not found", show_id=show_id)
    if show.start_time <= datetime.now():
        raise InvalidState("Show has already started", show_id=show_id)
    if await session.get(User, user_id) is None:
        raise NotFound("User not found", user_id=user_id)

    priced = (
        await session.execute(
            select(Seat.seat_id, ShowSeatPrice.price)
            .join(
                ShowSeatPrice,
                (ShowSeatPrice.show_id == show_id) & (ShowSeatPrice.seat_type == Seat.seat_type),
            )
            .where(Seat.screen_id == show.screen_id, Seat.seat_id.in_(seat_ids))
        )
    ).all()
    if len(priced) != len(seat_ids):
        unknown = sorted(set(seat_ids) - {r.seat_id for r in priced})
        raise NotFound("Seats do not belong to this show", seat_ids=unknown)

    await _reclaim_expired_holds(session, show_id, seat_ids, now)

    booking = Booking(
        user_id=user_id,
        show_id=show_id,
        status=BookingStatus.PENDING,
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(booking)
    await session.flush()

    # The compare-and-set. Under READ COMMITTED a competing transaction blocks on
    # the row lock, then re-evaluates status against the committed row and skips it.
    claimed = await session.execute(
        update(ShowSeat)
        .where(
            ShowSeat.show_id == show_id,
            ShowSeat.seat_id.in_(seat_ids),
            ShowSeat.status == SeatStatus.AVAILABLE,
        )
        .values(status=SeatStatus.HELD, booking_id=booking.booking_id)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != len(seat_ids):
        raise SeatsUnavailable(seat_ids)  # rolls back the partial claim and the booking

    session.add_all(BookingSeat(booking_id=booking.booking_id, seat_id=r.seat_id, price=r.price) for r in priced)
    return booking.booking_id


async def _reclaim_expired_holds(session: AsyncSession, show_id: int, seat_ids: list[int], now: datetime) -> None:
    """Expire lapsed holds on exactly these seats, so a buyer never waits for the sweeper."""
    stale = (
        await session.execute(
            select(ShowSeat.booking_id)
            .join(Booking, Booking.booking_id == ShowSeat.booking_id)
            .where(
                ShowSeat.show_id == show_id,
                ShowSeat.seat_id.in_(seat_ids),
                ShowSeat.status == SeatStatus.HELD,
                Booking.status == BookingStatus.PENDING,
                Booking.expires_at <= now,
            )
            .distinct()
        )
    ).scalars().all()
    if stale:
        await release_bookings(session, list(stale), BookingStatus.EXPIRED, now)


async def release_bookings(
    session: AsyncSession,
    booking_ids: list[int],
    new_status: BookingStatus,
    now: datetime,
    *,
    require_expired: bool = True,
) -> list[ReleasedHold]:
    """Move PENDING bookings to a terminal state and hand their seats back.

    Re-checks state under FOR UPDATE, so a concurrent webhook that already
    confirmed the booking wins and nothing is released.
    """
    locked = (
        await session.execute(
            select(Booking)
            .where(Booking.booking_id.in_(booking_ids), Booking.status == BookingStatus.PENDING)
            .order_by(Booking.booking_id)
            .with_for_update()
        )
    ).scalars().all()

    released = []
    for booking in locked:
        if require_expired and booking.expires_at > now:
            continue
        booking.status = new_status
        booking.updated_at = now
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.booking_id == booking.booking_id, ShowSeat.status == SeatStatus.HELD)
            .values(status=SeatStatus.AVAILABLE, booking_id=None)
            .execution_options(synchronize_session=False)
        )
        seat_ids = (
            await session.execute(select(BookingSeat.seat_id).where(BookingSeat.booking_id == booking.booking_id))
        ).scalars().all()
        released.append(ReleasedHold(booking.booking_id, booking.show_id, list(seat_ids)))
    return released


async def expire_due_holds(ctx: Container) -> int:
    """Sweeper body: expire overdue holds in batches. Safe to run in every process —
    SKIP LOCKED hands each process a disjoint batch."""
    batch_size = ctx.settings.sweeper_batch_size

    async def sweep_batch(session: AsyncSession) -> int:
        now = utcnow()
        due = (
            await session.execute(
                select(Booking.booking_id)
                .where(Booking.status == BookingStatus.PENDING, Booking.expires_at <= now)
                .order_by(Booking.expires_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        if due:
            await release_bookings(session, list(due), BookingStatus.EXPIRED, now)
        return len(due)

    total = 0
    while True:
        swept = await run_in_transaction(ctx.sessionmaker, sweep_batch)
        total += swept
        if swept < batch_size:
            return total


async def cancel_hold(ctx: Container, booking_id: int, user_id: int) -> BookingOut:
    async def cancel(session: AsyncSession) -> list[ReleasedHold]:
        booking = await session.get(Booking, booking_id)
        if booking is None or booking.user_id != user_id:
            raise NotFound("Booking not found", booking_id=booking_id)
        released = await release_bookings(
            session, [booking_id], BookingStatus.CANCELLED, utcnow(), require_expired=False
        )
        if not released:
            raise InvalidState("Only a pending hold can be cancelled", booking_id=booking_id)
        return released

    for hold in await run_in_transaction(ctx.sessionmaker, cancel):
        await ctx.seat_locks.forget(hold.show_id, hold.seat_ids)
    return await get_booking(ctx, booking_id)


async def get_booking(ctx: Container, booking_id: int) -> BookingOut:
    async with ctx.read_sessionmaker() as session:
        booking = await session.get(Booking, booking_id)
        if booking is None:
            raise NotFound("Booking not found", booking_id=booking_id)
        return await _booking_out(session, booking)


async def _find_by_idempotency_key(ctx: Container, user_id: int, key: str) -> BookingOut | None:
    async with ctx.read_sessionmaker() as session:
        booking = (
            await session.execute(
                select(Booking).where(Booking.user_id == user_id, Booking.idempotency_key == key)
            )
        ).scalar_one_or_none()
        return await _booking_out(session, booking) if booking else None


async def _booking_out(session: AsyncSession, booking: Booking) -> BookingOut:
    seats = (
        await session.execute(
            select(Seat.seat_id, Seat.row_label, Seat.seat_number, BookingSeat.price)
            .join(BookingSeat, BookingSeat.seat_id == Seat.seat_id)
            .where(BookingSeat.booking_id == booking.booking_id)
            .order_by(Seat.row_label, Seat.seat_number)
        )
    ).all()
    booked = [BookedSeat.model_validate(s, from_attributes=True) for s in seats]
    return BookingOut(
        booking_id=booking.booking_id,
        user_id=booking.user_id,
        show_id=booking.show_id,
        status=booking.status,
        expires_at=as_utc(booking.expires_at),
        total_amount=sum((s.price for s in booked), start=0),
        seats=booked,
    )
