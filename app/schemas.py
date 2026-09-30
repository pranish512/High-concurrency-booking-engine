from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models import BookingStatus, PaymentStatus, SeatStatus, SeatType


class DateSlot(BaseModel):
    date: date
    label: str
    show_count: int


class ShowTime(BaseModel):
    show_id: int
    start_time: datetime
    time_label: str
    screen: str
    seats_available: int
    seats_total: int


class MovieShowtimes(BaseModel):
    movie_id: int
    title: str
    certification: str
    duration_min: int
    language: str
    format: str
    shows: list[ShowTime]


class TheatreShowtimes(BaseModel):
    theatre_id: int
    theatre_name: str
    date: date
    movies: list[MovieShowtimes]


class SeatOut(BaseModel):
    seat_id: int
    row_label: str
    seat_number: int
    seat_type: SeatType
    price: Decimal
    status: SeatStatus


class SeatMap(BaseModel):
    show_id: int
    start_time: datetime
    seats: list[SeatOut]


class HoldRequest(BaseModel):
    user_id: int = Field(gt=0)
    seat_ids: list[int] = Field(min_length=1, max_length=50)


class BookedSeat(BaseModel):
    seat_id: int
    row_label: str
    seat_number: int
    price: Decimal


class BookingOut(BaseModel):
    booking_id: int
    user_id: int
    show_id: int
    status: BookingStatus
    expires_at: datetime
    total_amount: Decimal
    seats: list[BookedSeat]


class PaymentCreate(BaseModel):
    user_id: int = Field(gt=0)


class PaymentOut(BaseModel):
    payment_id: int
    booking_id: int
    gateway_order_id: str
    amount: Decimal
    currency: str
    status: PaymentStatus
    pay_before: datetime


class WebhookEvent(BaseModel):
    """What the (mock) gateway POSTs, signed with HMAC-SHA256 over the raw body."""

    event_id: str = Field(min_length=1, max_length=64)
    event_type: Literal["payment.succeeded", "payment.failed"]
    gateway_order_id: str = Field(min_length=1, max_length=64)
    gateway_payment_id: str | None = Field(default=None, max_length=64)
    amount: Decimal


class SimulatePayment(BaseModel):
    gateway_order_id: str
    outcome: Literal["succeeded", "failed"] = "succeeded"
