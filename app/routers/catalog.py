from datetime import date

from fastapi import APIRouter, Depends, Query

from app.container import Container, get_container
from app.schemas import DateSlot, SeatMap, TheatreShowtimes
from app.services import catalog

router = APIRouter(tags=["catalog"])


@router.get("/theatres/{theatre_id}/dates", response_model=list[DateSlot])
async def next_seven_dates(theatre_id: int, ctx: Container = Depends(get_container)):
    """The date picker: today plus the next six days, with a show count for each."""
    return await catalog.list_dates(ctx, theatre_id, today=date.today())


@router.get("/theatres/{theatre_id}/showtimes", response_model=TheatreShowtimes)
async def showtimes(
    theatre_id: int,
    show_date: date = Query(alias="date"),
    ctx: Container = Depends(get_container),
):
    """P2: all shows at a theatre on a date, grouped per movie with their timings."""
    return await catalog.theatre_showtimes(ctx, theatre_id, show_date)


@router.get("/shows/{show_id}/seats", response_model=SeatMap)
async def seat_map(show_id: int, ctx: Container = Depends(get_container)):
    return await catalog.seat_map(ctx, show_id)
