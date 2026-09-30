from fastapi import APIRouter, Depends, Header, Query, status

from app.container import Container, get_container
from app.schemas import BookingOut, HoldRequest, PaymentCreate, PaymentOut
from app.services import bookings, payments

router = APIRouter(tags=["bookings"])


@router.post("/shows/{show_id}/holds", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
async def hold_seats(
    show_id: int,
    body: HoldRequest,
    idempotency_key: str | None = Header(default=None, max_length=64),
    ctx: Container = Depends(get_container),
):
    """Hold seats for `hold_ttl_seconds`. 409 if any seat is taken; 503 when shedding load.

    Send an `Idempotency-Key` header to make client retries safe.
    """
    return await bookings.create_hold(
        ctx, show_id=show_id, user_id=body.user_id, seat_ids=body.seat_ids, idempotency_key=idempotency_key
    )


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_booking(booking_id: int, ctx: Container = Depends(get_container)):
    return await bookings.get_booking(ctx, booking_id)


@router.delete("/bookings/{booking_id}/hold", response_model=BookingOut)
async def cancel_hold(booking_id: int, user_id: int = Query(gt=0), ctx: Container = Depends(get_container)):
    """Release a pending hold early (user backed out)."""
    return await bookings.cancel_hold(ctx, booking_id, user_id)


@router.post("/bookings/{booking_id}/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def start_payment(booking_id: int, body: PaymentCreate, ctx: Container = Depends(get_container)):
    """Create (or reuse) a gateway order for a pending booking."""
    return await payments.create_payment(ctx, booking_id, body.user_id)
