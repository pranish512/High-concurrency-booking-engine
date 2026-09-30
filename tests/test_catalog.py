from datetime import date, timedelta

from tests.conftest import hold


async def test_date_picker_lists_next_seven_days(client, world):
    days = (await client.get(f"/theatres/{world.theatre_id}/dates")).json()

    assert [d["date"] for d in days] == [(date.today() + timedelta(days=i)).isoformat() for i in range(7)]
    counts = {d["date"]: d["show_count"] for d in days}
    assert counts[world.show_date.isoformat()] == 2
    assert counts[date.today().isoformat()] == 0


async def test_showtimes_groups_shows_per_movie(client, world):
    resp = await client.get(f"/theatres/{world.theatre_id}/showtimes", params={"date": world.show_date.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["theatre_name"] == "Cineplex Lower Parel"
    [movie] = body["movies"]
    assert (movie["title"], movie["language"], movie["format"]) == ("Skyline Heist", "Hindi", "2D")
    assert [s["time_label"] for s in movie["shows"]] == ["06:00 PM", "09:30 PM"]
    assert movie["shows"][0]["seats_available"] == movie["shows"][0]["seats_total"] == 30


async def test_showtimes_are_cached_briefly(client, ctx, world):
    params = {"date": world.show_date.isoformat()}
    await client.get(f"/theatres/{world.theatre_id}/showtimes", params=params)
    await hold(client, world.show_id, world.user_ids[0], world.seat_ids[:1])

    cached = (await client.get(f"/theatres/{world.theatre_id}/showtimes", params=params)).json()
    assert cached["movies"][0]["shows"][0]["seats_available"] == 30  # served from cache
    assert await ctx.redis.ttl(f"showtimes:{world.theatre_id}:{world.show_date.isoformat()}") > 0


async def test_unknown_theatre_is_404(client, world):
    assert (await client.get("/theatres/999/showtimes", params={"date": "2026-10-01"})).status_code == 404
