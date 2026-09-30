"""ORM mapping of sql/01_schema.sql. The SQL file is the source of truth for MySQL;
these models are what the app queries with (and what tests create on SQLite)."""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.clock import utcnow

# BIGINT in MySQL; SQLite only auto-increments "INTEGER PRIMARY KEY".
BigId = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class SeatType(enum.StrEnum):
    REGULAR = "REGULAR"
    PREMIUM = "PREMIUM"
    RECLINER = "RECLINER"


class ShowFormat(enum.StrEnum):
    TWO_D = "2D"
    THREE_D = "3D"
    IMAX_2D = "IMAX_2D"
    IMAX_3D = "IMAX_3D"
    FOUR_DX = "4DX"


class ShowStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"


class SeatStatus(enum.StrEnum):
    AVAILABLE = "AVAILABLE"
    HELD = "HELD"
    BOOKED = "BOOKED"


class BookingStatus(enum.StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PaymentStatus(enum.StrEnum):
    CREATED = "CREATED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"


def _enum(cls: type[enum.Enum]) -> Enum:
    return Enum(cls, values_callable=lambda e: [m.value for m in e], name=cls.__name__.lower())


class City(Base):
    __tablename__ = "cities"
    __table_args__ = (UniqueConstraint("name", "state"),)

    city_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))


class Language(Base):
    __tablename__ = "languages"

    language_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str] = mapped_column(String(15), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Movie(Base):
    __tablename__ = "movies"

    movie_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    duration_min: Mapped[int] = mapped_column(Integer)
    certification: Mapped[str] = mapped_column(String(2))
    release_date: Mapped[date] = mapped_column(Date)


class Theatre(Base):
    __tablename__ = "theatres"

    theatre_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.city_id"))
    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str] = mapped_column(String(255))


class Screen(Base):
    __tablename__ = "screens"
    __table_args__ = (UniqueConstraint("theatre_id", "name"),)

    screen_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theatre_id: Mapped[int] = mapped_column(ForeignKey("theatres.theatre_id"))
    name: Mapped[str] = mapped_column(String(50))


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("screen_id", "row_label", "seat_number"),)

    seat_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.screen_id"))
    row_label: Mapped[str] = mapped_column(String(3))
    seat_number: Mapped[int] = mapped_column(Integer)
    seat_type: Mapped[SeatType] = mapped_column(_enum(SeatType))


class Show(Base):
    __tablename__ = "shows"
    __table_args__ = (UniqueConstraint("screen_id", "start_time"),)

    show_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.screen_id"))
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.movie_id"))
    language_id: Mapped[int] = mapped_column(ForeignKey("languages.language_id"))
    format: Mapped[ShowFormat] = mapped_column(_enum(ShowFormat), default=ShowFormat.TWO_D)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[ShowStatus] = mapped_column(_enum(ShowStatus), default=ShowStatus.SCHEDULED)


class ShowSeatPrice(Base):
    __tablename__ = "show_seat_prices"

    show_id: Mapped[int] = mapped_column(ForeignKey("shows.show_id"), primary_key=True)
    seat_type: Mapped[SeatType] = mapped_column(_enum(SeatType), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("idx_bookings_status_expiry", "status", "expires_at"),
    )

    booking_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.show_id"))
    status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus), default=BookingStatus.PENDING)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ShowSeat(Base):
    __tablename__ = "show_seats"

    show_id: Mapped[int] = mapped_column(ForeignKey("shows.show_id"), primary_key=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.seat_id"), primary_key=True)
    status: Mapped[SeatStatus] = mapped_column(_enum(SeatStatus), default=SeatStatus.AVAILABLE)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.booking_id"), index=True)


class BookingSeat(Base):
    __tablename__ = "booking_seats"

    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.booking_id"), primary_key=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.seat_id"), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Payment(Base):
    __tablename__ = "payments"

    payment_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.booking_id"), index=True)
    gateway: Mapped[str] = mapped_column(String(30))
    gateway_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[PaymentStatus] = mapped_column(_enum(PaymentStatus), default=PaymentStatus.CREATED)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    gateway_order_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    outcome: Mapped[str | None] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
