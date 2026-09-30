"""Seat-rush load test.

Thousands of simulated users fight over a deliberately small pool of "hot" seats
on one show, then pay; the gateway's webhooks are replayed with duplicates. A 409
on a hold is the expected outcome of losing a race, so it is recorded as a
success under its own name ("hold [lost race]") — errors are 5xx, timeouts, or
anything unexpected. Afterwards, run verify_invariants.py.

    locust -f loadtest/locustfile.py --host http://localhost:8000
"""

# locust must be imported first: it gevent-patches ssl before requests loads it.
from locust import HttpUser, between, events, task  # isort: skip

import hashlib
import hmac
import json
import os
import random
import uuid
from datetime import date, timedelta

import requests

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev-webhook-secret")
USER_POOL = int(os.getenv("LT_USER_POOL", "10000"))
HOT_SEATS = int(os.getenv("LT_HOT_SEATS", "80"))  # smaller = more contention
THEATRE_ID = int(os.getenv("LT_THEATRE_ID", "1"))
SHOW_TITLE = os.getenv("LT_SHOW_TITLE", "Load Test Premiere")
PAY_RATE = float(os.getenv("LT_PAY_RATE", "0.8"))            # winners who go on to pay
PAY_SUCCESS_RATE = float(os.getenv("LT_PAY_SUCCESS_RATE", "0.9"))
DUPLICATE_WEBHOOK_RATE = float(os.getenv("LT_DUP_WEBHOOK_RATE", "0.3"))

TARGET: dict = {"show_id": None, "hot_seats": [], "date": None}


@events.test_start.add_listener
def discover_show(environment, **_):
    """Find the load-test show via the public API (it is tomorrow at theatre 1)."""
    day = date.today() + timedelta(days=1)
    show_id = os.getenv("LT_SHOW_ID")
    if not show_id:
        showtimes = requests.get(
            f"{environment.host}/theatres/{THEATRE_ID}/showtimes", params={"date": day.isoformat()}, timeout=10
        ).json()
        matches = [m for m in showtimes["movies"] if m["title"] == SHOW_TITLE]
        if not matches:
            raise RuntimeError(f"'{SHOW_TITLE}' not found on {day}; load sql/04_loadtest_seed.sql first")
        show_id = matches[0]["shows"][0]["show_id"]
    seats = requests.get(f"{environment.host}/shows/{show_id}/seats", timeout=10).json()["seats"]
    TARGET.update(show_id=int(show_id), hot_seats=[s["seat_id"] for s in seats[:HOT_SEATS]], date=day)
    print(f"load test target: show {show_id}, {len(TARGET['hot_seats'])} hot seats")


def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


class SeatRusher(HttpUser):
    """Tries to grab 1-4 adjacent hot seats, then pays for them."""

    weight = 4
    wait_time = between(0.05, 0.5)

    @task
    def rush(self):
        seats = TARGET["hot_seats"]
        count = random.randint(1, 4)
        start = random.randrange(0, len(seats) - count + 1)
        user_id = random.randint(1, USER_POOL)
        idempotency_key = uuid.uuid4().hex

        with self.client.post(
            f"/shows/{TARGET['show_id']}/holds",
            json={"user_id": user_id, "seat_ids": seats[start:start + count]},
            headers={"Idempotency-Key": idempotency_key},
            name="hold",
            catch_response=True,
        ) as resp:
            if resp.status_code in (409, 503):  # lost the race / shed under load: expected
                resp.request_meta["name"] = "hold [lost race]" if resp.status_code == 409 else "hold [shed 503]"
                resp.success()
                return
            if resp.status_code != 201:
                resp.failure(f"unexpected {resp.status_code}: {resp.text[:200]}")
                return
            booking = resp.json()

        if random.random() > PAY_RATE:
            if random.random() < 0.5:  # half of the non-payers back out explicitly
                self.client.delete(f"/bookings/{booking['booking_id']}/hold", params={"user_id": user_id},
                                   name="cancel hold")
            return

        pay = self.client.post(f"/bookings/{booking['booking_id']}/payments", json={"user_id": user_id},
                               name="start payment")
        if pay.status_code != 201:
            return
        payment = pay.json()

        succeeded = random.random() < PAY_SUCCESS_RATE
        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "event_type": "payment.succeeded" if succeeded else "payment.failed",
            "gateway_order_id": payment["gateway_order_id"],
            "gateway_payment_id": f"pay_{uuid.uuid4().hex[:20]}" if succeeded else None,
            "amount": payment["amount"],
        }
        body = json.dumps(event).encode()
        headers = {"Content-Type": "application/json", "X-Webhook-Signature": _sign(body)}
        deliveries = 2 if random.random() < DUPLICATE_WEBHOOK_RATE else 1  # gateway retries
        for _ in range(deliveries):
            self.client.post("/payments/webhook", data=body, headers=headers, name="payment webhook")


class Browser(HttpUser):
    """Read traffic: date picker, showtimes (P2), seat map."""

    weight = 1
    wait_time = between(0.2, 1.0)

    @task(3)
    def showtimes(self):
        self.client.get(f"/theatres/{THEATRE_ID}/showtimes", params={"date": TARGET["date"].isoformat()},
                        name="showtimes")

    @task(1)
    def dates(self):
        self.client.get(f"/theatres/{THEATRE_ID}/dates", name="dates")

    @task(2)
    def seat_map(self):
        self.client.get(f"/shows/{TARGET['show_id']}/seats", name="seat map")
