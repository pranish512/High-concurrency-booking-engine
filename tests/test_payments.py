from sqlalchemy import func, select

from app.models import Payment, PaymentStatus, PaymentWebhookEvent, SeatStatus, ShowSeat
from app.schemas import WebhookEvent
from app.services.payments import process_webhook_event
from tests.conftest import drain_webhooks, force_expire, hold, post_webhook


async def held_booking_with_payment(client, world, seats, user_index=0):
    user_id = world.user_ids[user_index]
    booking = (await hold(client, world.show_id, user_id, seats)).json()
    payment = (await client.post(f"/bookings/{booking['booking_id']}/payments", json={"user_id": user_id})).json()
    return booking, payment


def success_event(payment, event_id="evt_1"):
    return {
        "event_id": event_id,
        "event_type": "payment.succeeded",
        "gateway_order_id": payment["gateway_order_id"],
        "gateway_payment_id": f"pay_{event_id}",
        "amount": payment["amount"],
    }


async def test_successful_payment_confirms_booking(client, ctx, world):
    seats = world.seat_ids[:2]
    booking, payment = await held_booking_with_payment(client, world, seats)
    assert payment["amount"] == "400.00"

    assert (await post_webhook(client, success_event(payment))).status_code == 202
    assert await drain_webhooks(ctx) == 1

    assert (await client.get(f"/bookings/{booking['booking_id']}")).json()["status"] == "CONFIRMED"
    async with ctx.sessionmaker() as s:
        statuses = (await s.execute(select(ShowSeat.status).where(ShowSeat.seat_id.in_(seats), ShowSeat.show_id == world.show_id))).scalars().all()
    assert statuses == [SeatStatus.BOOKED, SeatStatus.BOOKED]

    # booked seats are rejected by the Redis fast path, even after hold keys are gone
    await ctx.redis.delete(*[k async for k in ctx.redis.scan_iter("seat:hold:*")])
    assert (await hold(client, world.show_id, world.user_ids[1], seats[:1])).status_code == 409


async def test_duplicate_webhooks_are_applied_once(client, ctx, world):
    _, payment = await held_booking_with_payment(client, world, world.seat_ids[2:3])
    event = success_event(payment, "evt_dup")

    for _ in range(3):
        assert (await post_webhook(client, event)).status_code == 202
    assert await drain_webhooks(ctx) == 3

    async with ctx.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(PaymentWebhookEvent)) == 1
        stored = await s.get(PaymentWebhookEvent, "evt_dup")
    assert stored.outcome == "CONFIRMED"
    # direct call reports the duplicate explicitly
    assert await process_webhook_event(ctx, WebhookEvent(**event)) == "DUPLICATE"


async def test_retrying_payment_reuses_open_order(client, world):
    booking, payment = await held_booking_with_payment(client, world, world.seat_ids[3:4])
    again = await client.post(f"/bookings/{booking['booking_id']}/payments", json={"user_id": world.user_ids[0]})
    assert again.json()["gateway_order_id"] == payment["gateway_order_id"]


async def test_failed_payment_keeps_hold_for_retry(client, ctx, world):
    booking, payment = await held_booking_with_payment(client, world, world.seat_ids[4:5])
    failed = {**success_event(payment, "evt_fail"), "event_type": "payment.failed", "gateway_payment_id": None}

    await post_webhook(client, failed)
    await drain_webhooks(ctx)

    assert (await client.get(f"/bookings/{booking['booking_id']}")).json()["status"] == "PENDING"
    retry = (await client.post(f"/bookings/{booking['booking_id']}/payments", json={"user_id": world.user_ids[0]})).json()
    assert retry["gateway_order_id"] != payment["gateway_order_id"]


async def test_payment_after_hold_expired_is_refunded_not_booked(client, ctx, world):
    seat = world.seat_ids[5]
    booking, payment = await held_booking_with_payment(client, world, [seat])
    await force_expire(ctx, booking["booking_id"])
    await ctx.redis.flushall()
    # someone else grabs the released seat
    assert (await hold(client, world.show_id, world.user_ids[1], [seat])).status_code == 201

    await post_webhook(client, success_event(payment, "evt_late"))
    await drain_webhooks(ctx)

    assert (await client.get(f"/bookings/{booking['booking_id']}")).json()["status"] == "EXPIRED"
    async with ctx.sessionmaker() as s:
        p = (await s.execute(select(Payment).where(Payment.gateway_order_id == payment["gateway_order_id"]))).scalar_one()
    assert p.status == PaymentStatus.REFUND_PENDING


async def test_amount_mismatch_is_refunded(client, ctx, world):
    booking, payment = await held_booking_with_payment(client, world, world.seat_ids[6:7])
    await post_webhook(client, {**success_event(payment, "evt_amt"), "amount": "1.00"})
    await drain_webhooks(ctx)

    assert (await client.get(f"/bookings/{booking['booking_id']}")).json()["status"] == "PENDING"
    async with ctx.sessionmaker() as s:
        assert (await s.get(PaymentWebhookEvent, "evt_amt")).outcome == "REFUND_AMOUNT_MISMATCH"


async def test_bad_signature_is_rejected(client, ctx, world):
    _, payment = await held_booking_with_payment(client, world, world.seat_ids[7:8])
    resp = await post_webhook(client, success_event(payment), secret="wrong")
    assert resp.status_code == 401
    assert await drain_webhooks(ctx) == 0


async def test_dev_gateway_simulation_round_trip(client, ctx, world):
    booking, payment = await held_booking_with_payment(client, world, world.seat_ids[8:9])
    resp = await client.post("/dev/gateway/simulate", json={"gateway_order_id": payment["gateway_order_id"]})
    assert resp.status_code == 202
    await drain_webhooks(ctx)
    assert (await client.get(f"/bookings/{booking['booking_id']}")).json()["status"] == "CONFIRMED"
