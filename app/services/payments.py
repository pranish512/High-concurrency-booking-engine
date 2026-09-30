"""Payment attempts and idempotent webhook processing.

Webhooks are at-least-once: gateways retry on timeouts and may deliver out of
order. Processing is made exactly-once-in-effect by inserting the event_id into
payment_webhook_events in the SAME transaction as the state change. A redelivery
hits the primary key, rolls back, and is reported as a duplicate.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import as_utc, utcnow
from app.container import Container
from app.db import run_in_transaction
from app.errors import InvalidSignature, InvalidState, NotFound
from app.models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Payment,
    PaymentStatus,
    PaymentWebhookEvent,
    SeatStatus,
    ShowSeat,
)
from app.schemas import PaymentOut, WebhookEvent
from app.services.bookings import release_bookings

GATEWAY = "mockpay"
SIGNATURE_HEADER = "X-Webhook-Signature"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not signature or not hmac.compare_digest(sign(secret, body), signature):
        raise InvalidSignature("Webhook signature mismatch")


async def create_payment(ctx: Container, booking_id: int, user_id: int) -> PaymentOut:
    async def create(session: AsyncSession) -> PaymentOut:
        now = utcnow()
        booking = (
            await session.execute(select(Booking).where(Booking.booking_id == booking_id).with_for_update())
        ).scalar_one_or_none()
        if booking is None or booking.user_id != user_id:
            raise NotFound("Booking not found", booking_id=booking_id)
        if booking.status != BookingStatus.PENDING or booking.expires_at <= now:
            raise InvalidState("Booking is not awaiting payment", booking_id=booking_id, status=booking.status)

        # Retrying "pay" reuses the open attempt instead of creating a second order.
        payment = (
            await session.execute(
                select(Payment).where(Payment.booking_id == booking_id, Payment.status == PaymentStatus.CREATED)
            )
        ).scalars().first()
        if payment is None:
            amount = await session.scalar(
                select(func.sum(BookingSeat.price)).where(BookingSeat.booking_id == booking_id)
            )
            payment = Payment(
                booking_id=booking_id,
                gateway=GATEWAY,
                gateway_order_id=f"order_{uuid.uuid4().hex}",
                amount=amount,
                currency="INR",
                status=PaymentStatus.CREATED,
                created_at=now,
                updated_at=now,
            )
            session.add(payment)
            await session.flush()
        return PaymentOut(
            payment_id=payment.payment_id,
            booking_id=booking_id,
            gateway_order_id=payment.gateway_order_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            pay_before=as_utc(booking.expires_at),
        )

    return await run_in_transaction(ctx.sessionmaker, create)


async def enqueue_webhook(ctx: Container, body: bytes) -> str:
    """Accept fast, process asynchronously: a Redis Stream absorbs gateway bursts."""
    return await ctx.redis.xadd(
        ctx.settings.webhook_stream, {"body": body.decode()}, maxlen=1_000_000, approximate=True
    )


@dataclass
class WebhookResult:
    outcome: str
    booked: tuple[int, list[int]] | None = None  # (show_id, seat_ids) confirmed by this event


async def process_webhook_event(ctx: Container, event: WebhookEvent) -> str:
    async def apply(session: AsyncSession) -> WebhookResult:
        now = utcnow()
        record = PaymentWebhookEvent(
            event_id=event.event_id,
            gateway_order_id=event.gateway_order_id,
            event_type=event.event_type,
            payload=json.loads(event.model_dump_json()),
            received_at=now,
        )
        session.add(record)
        await session.flush()  # duplicate event_id -> IntegrityError -> whole txn rolls back

        payment = (
            await session.execute(
                select(Payment).where(Payment.gateway_order_id == event.gateway_order_id).with_for_update()
            )
        ).scalar_one_or_none()

        if payment is None:
            result = WebhookResult("UNKNOWN_ORDER")
        elif payment.status != PaymentStatus.CREATED:
            # Out-of-order or conflicting event for a payment that already settled.
            result = WebhookResult(f"IGNORED_{payment.status}")
        elif event.event_type == "payment.failed":
            payment.status = PaymentStatus.FAILED
            payment.updated_at = now
            result = WebhookResult("PAYMENT_FAILED")  # hold stays until expiry so the user can retry
        else:
            result = await _apply_success(session, payment, event, now)

        record.outcome = result.outcome
        record.processed_at = now
        return result

    try:
        result = await run_in_transaction(ctx.sessionmaker, apply)
    except IntegrityError:
        async with ctx.read_sessionmaker() as session:
            if await session.get(PaymentWebhookEvent, event.event_id) is not None:
                return "DUPLICATE"
        raise

    if result.booked:
        await ctx.seat_locks.mark_booked(*result.booked)
    return result.outcome


async def _apply_success(session: AsyncSession, payment: Payment, event: WebhookEvent, now) -> WebhookResult:
    payment.gateway_payment_id = event.gateway_payment_id
    payment.updated_at = now

    if Decimal(event.amount) != payment.amount:
        payment.status = PaymentStatus.REFUND_PENDING
        return WebhookResult("REFUND_AMOUNT_MISMATCH")

    booking = (
        await session.execute(select(Booking).where(Booking.booking_id == payment.booking_id).with_for_update())
    ).scalar_one()

    if booking.status != BookingStatus.PENDING:
        # Money arrived after the hold expired / was cancelled / another attempt won.
        payment.status = PaymentStatus.REFUND_PENDING
        return WebhookResult(f"REFUND_BOOKING_{booking.status}")

    wanted = (
        await session.execute(select(BookingSeat.seat_id).where(BookingSeat.booking_id == booking.booking_id))
    ).scalars().all()
    held = (
        await session.execute(
            select(ShowSeat.seat_id)
            .where(ShowSeat.booking_id == booking.booking_id, ShowSeat.status == SeatStatus.HELD)
            .with_for_update()
        )
    ).scalars().all()

    if len(held) != len(wanted):  # defensive: PENDING should always mean every seat is held
        await release_bookings(session, [booking.booking_id], BookingStatus.FAILED, now, require_expired=False)
        payment.status = PaymentStatus.REFUND_PENDING
        return WebhookResult("REFUND_HOLD_LOST")

    # A booking still PENDING slightly past expires_at (sweeper not yet run) is
    # honoured: its seats were never handed to anyone else.
    await session.execute(
        update(ShowSeat)
        .where(ShowSeat.booking_id == booking.booking_id, ShowSeat.status == SeatStatus.HELD)
        .values(status=SeatStatus.BOOKED)
        .execution_options(synchronize_session=False)
    )
    booking.status = BookingStatus.CONFIRMED
    booking.updated_at = now
    payment.status = PaymentStatus.SUCCEEDED
    return WebhookResult("CONFIRMED", booked=(booking.show_id, list(wanted)))


async def build_simulated_event(ctx: Container, gateway_order_id: str, outcome: str) -> bytes:
    """Dev helper: what the gateway would send for an order."""
    async with ctx.read_sessionmaker() as session:
        payment = (
            await session.execute(select(Payment).where(Payment.gateway_order_id == gateway_order_id))
        ).scalar_one_or_none()
    if payment is None:
        raise NotFound("Payment order not found", gateway_order_id=gateway_order_id)
    event = WebhookEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        event_type=f"payment.{outcome}",
        gateway_order_id=gateway_order_id,
        gateway_payment_id=f"pay_{uuid.uuid4().hex[:20]}" if outcome == "succeeded" else None,
        amount=payment.amount,
    )
    return event.model_dump_json().encode()
