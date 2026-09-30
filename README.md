# High-Concurrency Booking Engine

A BookMyShow-style ticketing backend built with FastAPI, SQLite and Redis, for the case where thousands of users compete for the same seats.

- **No double booking.** A guarded compare-and-set on the per-show seat row, with SQLite writers serialized by `BEGIN IMMEDIATE`. A Redis Lua fast path rejects rush losers in about 1 ms, before they reach the database.
- **Timed holds.** A hold lasts 5 minutes and is released automatically by a background sweeper and by lazy reclaim.
- **Idempotent payment webhooks.** HMAC-verified, queued on a Redis Stream, and applied exactly once through an event-id ledger.
- **Burst handling.** Per-process admission control (503 + `Retry-After`) and a queue-backed webhook intake.
- **Proof.** Concurrency tests, an end-to-end API check, a Locust seat-rush load test, and a post-run invariant audit.

📄 **P1 / P2 submission doc:** [docs/SCHEMA.md](docs/SCHEMA.md). It covers the tables, attributes, sample rows, normalization, locking strategy and the P2 query. The MySQL SQL the brief asks for is in [`sql/mysql/`](sql/mysql); the SQLite version the app runs on is in [`sql/sqlite/`](sql/sqlite).

---

## Architecture

```
            ┌──────────────────── FastAPI (uvicorn) ─────────────────────┐
 client ──► │ holds ─► Redis Lua fast path ─► admission gate ─► writer  │──► SQLite (WAL)
            │ reads ─────────────────────────────────────────► readers │      show_seats guarded UPDATE
            │ webhook ─► verify HMAC ─► XADD stream ─► 202              │      bookings / payments / ledger
            │ background: hold sweeper · stream consumer group          │
            └───────────────────────────────────────────────────────────┘
                              ▲            │
                              └── Redis ◄──┘  hold keys w/ TTL, booked-seat sets, webhook stream, showtimes cache
```

| Path | Module |
|---|---|
| SQLite migrations and seed data | [`sql/sqlite/`](sql/sqlite), runner [`app/migrate.py`](app/migrate.py) |
| MySQL scripts (P1/P2 submission) | [`sql/mysql/`](sql/mysql) |
| Engines (writer/reader split) | [`app/db.py`](app/db.py) |
| Seat hold, release, expiry | [`app/services/bookings.py`](app/services/bookings.py) |
| Redis Lua seat locks | [`app/seat_locks.py`](app/seat_locks.py) |
| Payments and webhooks | [`app/services/payments.py`](app/services/payments.py) |
| Sweeper and stream consumer | [`app/workers.py`](app/workers.py) |
| Showtimes (P2) and seat map | [`app/services/catalog.py`](app/services/catalog.py) |
| API check, load test, audit | [`scripts/check_api.py`](scripts/check_api.py), [`loadtest/`](loadtest) |

## Run it

You need Python 3.12+ and a Redis 6.2+ server on `127.0.0.1:6379`. The Windows commands are below; on macOS/Linux, use `.venv/bin/` instead of `.venv/Scripts/`.

**1. Install the dependencies**

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements-dev.txt
```

**2. Create the database.** This creates `data/booking.db` and applies the schema, sample data and load-test data. Every file is recorded, so re-running only applies new ones; `--reset` starts over.

```bash
.venv/Scripts/python -m app.migrate --seed
```

**3. Start the API**

```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

The interactive docs are at http://127.0.0.1:8000/docs.

**4. Check every endpoint** end to end, including a 50-user race for one seat and duplicate webhooks. The script prints PASS/FAIL per step.

```bash
.venv/Scripts/python -m scripts.check_api
```

**5. Audit the data**

```bash
.venv/Scripts/python -m loadtest.verify_invariants
```

Settings are in [`app/config.py`](app/config.py) and can be overridden with environment variables or a `.env` file (see [`.env.example`](.env.example)). If you have Docker, `docker compose up --build` runs the API and Redis together.

### API

| Method | Path | Notes |
|---|---|---|
| GET | `/theatres/{id}/dates` | Today + 6 days, with show counts |
| GET | `/theatres/{id}/showtimes?date=YYYY-MM-DD` | P2, grouped per movie; cached for 30 s |
| GET | `/shows/{id}/seats` | Live seat map with prices |
| POST | `/shows/{id}/holds` | `{user_id, seat_ids}` + optional `Idempotency-Key`. 201 / 409 / 503 |
| GET | `/bookings/{id}` | |
| DELETE | `/bookings/{id}/hold?user_id=` | Release early |
| POST | `/bookings/{id}/payments` | Create (or reuse) a gateway order |
| POST | `/payments/webhook` | Gateway callback; header `X-Webhook-Signature` = hex HMAC-SHA256(body) |
| POST | `/dev/gateway/simulate` | Dev only: signs and queues a webhook for an order |

## How SQLite handles concurrency here

SQLite allows one writer at a time, so the app uses it deliberately (details in [docs/SCHEMA.md §7](docs/SCHEMA.md#7-running-on-sqlite)):

- **WAL mode.** Reads never block and never wait for the writer.
- **One writer connection per process, opened with `BEGIN IMMEDIATE`.** Write transactions queue on it asynchronously instead of failing with `SQLITE_BUSY`. The write lock is held from the first statement, so every check-then-write is atomic.
- **The guarded `UPDATE show_seats ... WHERE status='AVAILABLE'`** still decides the winner, and the `(show_id, seat_id)` primary key plus CHECK constraints guard the data.
- **The Redis fast path keeps losers off the single write slot**, so the writer only handles requests that can actually succeed.

## Tests

The tests build each database from the real SQLite migration and use fakeredis in place of a Redis server, so they need nothing running:

```bash
.venv/Scripts/python -m pytest -q
```

Covered: a 100-user rush on one seat (exactly one winner, and losers never reach the DB); overlapping multi-seat requests are all-or-nothing; the DB still blocks double booking after Redis is flushed; idempotent hold retries; sweeper expiry and lazy reclaim; cancel; 503 load shedding; webhook confirm; triple-delivered webhooks applied once; failed payment then retry; late payment after expiry → refund; amount mismatch; bad signature; the P2 showtimes grouping and cache.

## Load test

With the API running:

```bash
.venv/Scripts/locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 500 -r 100 -t 2m
```

```bash
.venv/Scripts/python -m loadtest.verify_invariants
```

Each simulated user picks 1–4 adjacent seats from a small pool of **hot seats** (`LT_HOT_SEATS`, default 80) on the 500-seat "Load Test Premiere" show, so contention is extreme. Winners pay (90 % succeed). 30 % of webhooks are delivered twice, as gateway retries would be. Some users cancel. A 409 is recorded as `hold [lost race]` and a 503 as `hold [shed 503]`: both are expected results, not failures. Read traffic (dates, showtimes, seat maps) runs alongside.

`verify_invariants` exits non-zero if any check returns rows:

- no seat is confirmed to two bookings
- every confirmed booking owns its seats as BOOKED
- no lost holds (every PENDING booking still holds all its seats)
- no orphan holds
- exactly one successful payment per confirmed booking
- captured money on an unconfirmed booking is always `REFUND_PENDING`
- overdue holds were swept

To start from a fresh dataset between runs:

```bash
.venv/Scripts/python -m app.migrate --reset --seed
```

## Assumptions and limits

- There is no authentication; `user_id` is passed in the request.
- Show times are theatre-local and the server runs in the theatres' timezone. Audit timestamps are UTC.
- The payment gateway is simulated (`mockpay`). Refunds are only *flagged* (`REFUND_PENDING`).
- SQLite caps write throughput at one transaction at a time per database file. That's fine for this project's data size; switching `DATABASE_URL` to `mysql+aiomysql://...` (schema in `sql/mysql/`) removes the limit without code changes.
- A webhook message that keeps failing is retried forever via `XAUTOCLAIM`. A production system would move it to a dead-letter stream after N attempts.
