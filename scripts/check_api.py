"""End-to-end check of every API against a running server.

    python -m scripts.check_api                      # http://127.0.0.1:8000
    python -m scripts.check_api --base-url http://host:port

Needs the seeded database (python -m app.migrate --seed) and the API running
with Redis. Prints PASS/FAIL per step; exit code 1 on any failure.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import date, timedelta

import httpx

from app.config import Settings
from app.services.payments import SIGNATURE_HEADER, sign

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail != "" else ""))
    if not ok:
        failures.append(name)


async def wait_for_status(client: httpx.AsyncClient, booking_id: int, wanted: str, timeout: float = 10) -> str:
    deadline = time.monotonic() + timeout
    while True:
        status = (await client.get(f"/bookings/{booking_id}")).json()["status"]
        if status == wanted or time.monotonic() > deadline:
            return status
        await asyncio.sleep(0.2)


async def signed_webhook(client: httpx.AsyncClient, event: dict, secret: str) -> httpx.Response:
    body = json.dumps(event).encode()
    return await client.post("/payments/webhook", content=body,
                             headers={"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, body)})


async def main(base_url: str) -> int:
    secret = Settings().webhook_secret
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        r = await client.get("/health")
        check("GET /health", r.status_code == 200, r.json())

        r = await client.get("/theatres/1/dates")
        days = r.json()
        check("GET /theatres/1/dates -> 7 dates", r.status_code == 200 and len(days) == 7,
              ", ".join(f"{d['label']}:{d['show_count']}" for d in days))

        r = await client.get("/theatres/1/showtimes", params={"date": tomorrow})
        movies = r.json()["movies"]
        check("GET /theatres/1/showtimes (P2)", r.status_code == 200 and movies,
              "; ".join(f"{m['title']} {[s['time_label'] for s in m['shows']]}" for m in movies))

        # Pick a regular show for tomorrow (not the load-test one) and its free seats.
        show = next(s for m in movies if m["title"] != "Load Test Premiere" for s in m["shows"])
        r = await client.get(f"/shows/{show['show_id']}/seats")
        free = [s["seat_id"] for s in r.json()["seats"] if s["status"] == "AVAILABLE"]
        check(f"GET /shows/{show['show_id']}/seats", r.status_code == 200 and len(free) >= 6, f"{len(free)} available")

        # --- seat rush: 50 users, one seat --------------------------------------------
        rush_seat = free[0]
        results = await asyncio.gather(*(
            client.post(f"/shows/{show['show_id']}/holds", json={"user_id": 100 + i, "seat_ids": [rush_seat]})
            for i in range(50)
        ))
        codes = [x.status_code for x in results]
        check("50 concurrent holds on one seat -> exactly one 201", codes.count(201) == 1 and codes.count(409) == 49,
              {c: codes.count(c) for c in set(codes)})

        # --- hold + idempotent retry ----------------------------------------------------
        key = uuid.uuid4().hex
        seats = free[1:3]
        first = await client.post(f"/shows/{show['show_id']}/holds", json={"user_id": 1, "seat_ids": seats},
                                  headers={"Idempotency-Key": key})
        retry = await client.post(f"/shows/{show['show_id']}/holds", json={"user_id": 1, "seat_ids": seats},
                                  headers={"Idempotency-Key": key})
        booking = first.json()
        check("POST /shows/{id}/holds", first.status_code == 201 and booking["status"] == "PENDING",
              f"booking {booking.get('booking_id')}, total {booking.get('total_amount')}")
        check("same Idempotency-Key returns the same booking", retry.json().get("booking_id") == booking["booking_id"])

        r = await client.post(f"/shows/{show['show_id']}/holds", json={"user_id": 2, "seat_ids": seats[:1]})
        check("holding an already-held seat -> 409", r.status_code == 409, r.json()["error"]["code"])

        r = await client.get(f"/bookings/{booking['booking_id']}")
        check("GET /bookings/{id}", r.status_code == 200 and r.json()["status"] == "PENDING")

        # --- payment + webhook (plus a duplicate delivery) ----------------------------
        r = await client.post(f"/bookings/{booking['booking_id']}/payments", json={"user_id": 1})
        payment = r.json()
        check("POST /bookings/{id}/payments", r.status_code == 201, f"{payment.get('gateway_order_id')} {payment.get('amount')}")

        event = {"event_id": f"evt_{uuid.uuid4().hex}", "event_type": "payment.succeeded",
                 "gateway_order_id": payment["gateway_order_id"], "gateway_payment_id": f"pay_{uuid.uuid4().hex[:16]}",
                 "amount": payment["amount"]}
        r1 = await signed_webhook(client, event, secret)
        r2 = await signed_webhook(client, event, secret)  # gateway retry
        check("POST /payments/webhook (twice) -> 202", r1.status_code == r2.status_code == 202)
        status = await wait_for_status(client, booking["booking_id"], "CONFIRMED")
        check("webhook consumer confirmed the booking", status == "CONFIRMED", status)

        r = await client.get(f"/shows/{show['show_id']}/seats")
        booked = {s["seat_id"] for s in r.json()["seats"] if s["status"] == "BOOKED"}
        check("seats show as BOOKED in the seat map", set(seats) <= booked)

        r = await signed_webhook(client, event, "wrong-secret")
        check("webhook with bad signature -> 401", r.status_code == 401)

        # --- cancel flow + dev gateway simulation --------------------------------------
        r = await client.post(f"/shows/{show['show_id']}/holds", json={"user_id": 3, "seat_ids": [free[3]]})
        cancel_id = r.json()["booking_id"]
        r = await client.delete(f"/bookings/{cancel_id}/hold", params={"user_id": 3})
        check("DELETE /bookings/{id}/hold -> CANCELLED", r.json().get("status") == "CANCELLED")
        r = await client.post(f"/shows/{show['show_id']}/holds", json={"user_id": 4, "seat_ids": [free[3]]})
        check("cancelled seat can be held again immediately", r.status_code == 201)

        sim_booking = r.json()["booking_id"]
        order = (await client.post(f"/bookings/{sim_booking}/payments", json={"user_id": 4})).json()
        r = await client.post("/dev/gateway/simulate", json={"gateway_order_id": order["gateway_order_id"]})
        check("POST /dev/gateway/simulate -> 202", r.status_code == 202)
        check("simulated payment confirmed", await wait_for_status(client, sim_booking, "CONFIRMED") == "CONFIRMED")

        r = await client.get("/theatres/999/showtimes", params={"date": tomorrow})
        check("unknown theatre -> 404", r.status_code == 404)

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    sys.exit(asyncio.run(main(parser.parse_args().base_url)))
