"""Read side: the 7-date picker, P2 showtimes, and the live seat map."""

import logging
from datetime import date, datetime, time, timedelta

from redis.exceptions import RedisError
from sqlalchemy import case, func, select

from app.container import Container
from app.errors import NotFound
from app.models import (
    Language,
    Movie,
    Screen,
    Seat,
    SeatStatus,
    Show,
    ShowSeat,
    ShowSeatPrice,
    ShowStatus,
    Theatre,
)
from app.schemas import DateSlot, MovieShowtimes, SeatMap, SeatOut, ShowTime, TheatreShowtimes

log = logging.getLogger(__name__)

PICKER_DAYS = 7


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


async def _get_theatre(session, theatre_id: int) -> Theatre:
    theatre = await session.get(Theatre, theatre_id)
    if theatre is None:
        raise NotFound("Theatre not found", theatre_id=theatre_id)
    return theatre


async def list_dates(ctx: Container, theatre_id: int, today: date) -> list[DateSlot]:
    window_start, _ = _day_bounds(today)
    window_end = window_start + timedelta(days=PICKER_DAYS)
    show_day = func.date(Show.start_time)
    async with ctx.read_sessionmaker() as session:
        await _get_theatre(session, theatre_id)
        rows = await session.execute(
            select(show_day, func.count())
            .join(Screen, Screen.screen_id == Show.screen_id)
            .where(
                Screen.theatre_id == theatre_id,
                Show.status == ShowStatus.SCHEDULED,
                Show.start_time >= window_start,
                Show.start_time < window_end,
            )
            .group_by(show_day)
        )
        # MySQL returns date objects, SQLite returns ISO strings.
        counts = {d if isinstance(d, date) else date.fromisoformat(d): n for d, n in rows}

    days = [today + timedelta(days=i) for i in range(PICKER_DAYS)]
    return [DateSlot(date=d, label=d.strftime("%a %d %b"), show_count=counts.get(d, 0)) for d in days]


def _showtimes_cache_key(theatre_id: int, day: date) -> str:
    return f"showtimes:{theatre_id}:{day.isoformat()}"


async def theatre_showtimes(ctx: Container, theatre_id: int, day: date) -> TheatreShowtimes:
    """P2: every show at a theatre on a date, grouped per movie/language/format.

    Read-heavy and identical for every visitor, so it is cached briefly in Redis;
    seat counts can lag by the TTL, the seat map itself is always live.
    """
    key = _showtimes_cache_key(theatre_id, day)
    try:
        cached = await ctx.redis.get(key)
    except RedisError:
        cached = None
    if cached:
        return TheatreShowtimes.model_validate_json(cached)

    day_start, day_end = _day_bounds(day)
    async with ctx.read_sessionmaker() as session:
        theatre = await _get_theatre(session, theatre_id)
        rows = (
            await session.execute(
                select(
                    Movie.movie_id,
                    Movie.title,
                    Movie.certification,
                    Movie.duration_min,
                    Language.name.label("language"),
                    Show.format,
                    Show.show_id,
                    Show.start_time,
                    Screen.name.label("screen"),
                )
                .select_from(Screen)
                .join(Show, Show.screen_id == Screen.screen_id)
                .join(Movie, Movie.movie_id == Show.movie_id)
                .join(Language, Language.language_id == Show.language_id)
                .where(
                    Screen.theatre_id == theatre_id,
                    Show.start_time >= day_start,
                    Show.start_time < day_end,
                    Show.status == ShowStatus.SCHEDULED,
                )
                .order_by(Movie.title, Language.name, Show.format, Show.start_time)
            )
        ).all()

        availability = {}
        if rows:
            counts = await session.execute(
                select(
                    ShowSeat.show_id,
                    func.sum(case((ShowSeat.status == SeatStatus.AVAILABLE, 1), else_=0)),
                    func.count(),
                )
                .where(ShowSeat.show_id.in_([r.show_id for r in rows]))
                .group_by(ShowSeat.show_id)
            )
            availability = {show_id: (int(free or 0), int(total)) for show_id, free, total in counts}

    grouped: dict[tuple, MovieShowtimes] = {}
    for r in rows:
        group_key = (r.movie_id, r.language, r.format)
        if group_key not in grouped:
            grouped[group_key] = MovieShowtimes(
                movie_id=r.movie_id,
                title=r.title,
                certification=r.certification,
                duration_min=r.duration_min,
                language=r.language,
                format=str(r.format),
                shows=[],
            )
        free, total = availability.get(r.show_id, (0, 0))
        grouped[group_key].shows.append(
            ShowTime(
                show_id=r.show_id,
                start_time=r.start_time,
                time_label=r.start_time.strftime("%I:%M %p"),
                screen=r.screen,
                seats_available=free,
                seats_total=total,
            )
        )

    result = TheatreShowtimes(
        theatre_id=theatre.theatre_id, theatre_name=theatre.name, date=day, movies=list(grouped.values())
    )
    try:
        await ctx.redis.set(key, result.model_dump_json(), ex=ctx.settings.showtimes_cache_ttl_seconds)
    except RedisError:
        log.warning("could not cache showtimes", exc_info=True)
    return result


async def seat_map(ctx: Container, show_id: int) -> SeatMap:
    async with ctx.read_sessionmaker() as session:
        show = await session.get(Show, show_id)
        if show is None:
            raise NotFound("Show not found", show_id=show_id)
        rows = await session.execute(
            select(Seat.seat_id, Seat.row_label, Seat.seat_number, Seat.seat_type, ShowSeatPrice.price, ShowSeat.status)
            .select_from(ShowSeat)
            .join(Seat, Seat.seat_id == ShowSeat.seat_id)
            .join(
                ShowSeatPrice,
                (ShowSeatPrice.show_id == ShowSeat.show_id) & (ShowSeatPrice.seat_type == Seat.seat_type),
            )
            .where(ShowSeat.show_id == show_id)
            .order_by(Seat.row_label, Seat.seat_number)
        )
        seats = [SeatOut.model_validate(r, from_attributes=True) for r in rows]
    return SeatMap(show_id=show.show_id, start_time=show.start_time, seats=seats)
