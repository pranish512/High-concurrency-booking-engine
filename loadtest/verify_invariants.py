"""Post-load-test audit. Every check must return zero rows.

    python -m loadtest.verify_invariants            # uses DATABASE_URL / .env
Exit code 1 if any invariant is violated.
"""

import sys
from datetime import timedelta

from sqlalchemy import create_engine, text

from app.clock import utcnow

from app.config import Settings

CHECKS = {
    "no seat confirmed to two bookings": """
        SELECT b.show_id, bs.seat_id, COUNT(*) AS confirmed_bookings
        FROM booking_seats bs JOIN bookings b ON b.booking_id = bs.booking_id
        WHERE b.status = 'CONFIRMED'
        GROUP BY b.show_id, bs.seat_id HAVING COUNT(*) > 1""",
    "every confirmed booking owns its seats as BOOKED": """
        SELECT b.booking_id, bs.seat_id, ss.status, ss.booking_id AS owner
        FROM bookings b
        JOIN booking_seats bs ON bs.booking_id = b.booking_id
        JOIN show_seats ss ON ss.show_id = b.show_id AND ss.seat_id = bs.seat_id
        WHERE b.status = 'CONFIRMED' AND (ss.status <> 'BOOKED' OR ss.booking_id <> b.booking_id)""",
    "no lost holds: every pending booking still holds all its seats": """
        SELECT b.booking_id, bs.seat_id, ss.status, ss.booking_id AS owner
        FROM bookings b
        JOIN booking_seats bs ON bs.booking_id = b.booking_id
        JOIN show_seats ss ON ss.show_id = b.show_id AND ss.seat_id = bs.seat_id
        WHERE b.status = 'PENDING' AND (ss.status <> 'HELD' OR ss.booking_id <> b.booking_id)""",
    "no orphan holds: HELD/BOOKED seats belong to a live booking": """
        SELECT ss.show_id, ss.seat_id, ss.status, b.status AS booking_status
        FROM show_seats ss JOIN bookings b ON b.booking_id = ss.booking_id
        WHERE (ss.status = 'HELD' AND b.status <> 'PENDING')
           OR (ss.status = 'BOOKED' AND b.status <> 'CONFIRMED')""",
    "every confirmed booking has exactly one successful payment": """
        SELECT b.booking_id, SUM(p.status = 'SUCCEEDED') AS succeeded
        FROM bookings b LEFT JOIN payments p ON p.booking_id = b.booking_id
        WHERE b.status = 'CONFIRMED'
        GROUP BY b.booking_id HAVING succeeded <> 1 OR succeeded IS NULL""",
    "no captured money on an unconfirmed booking (must be REFUND_PENDING)": """
        SELECT p.payment_id, p.booking_id, b.status
        FROM payments p JOIN bookings b ON b.booking_id = p.booking_id
        WHERE p.status = 'SUCCEEDED' AND b.status <> 'CONFIRMED'""",
    "overdue holds were swept": """
        SELECT booking_id, expires_at FROM bookings
        WHERE status = 'PENDING' AND expires_at < :cutoff""",  # 30 s grace for the sweeper
}

SUMMARY = """
    SELECT
      (SELECT COUNT(*) FROM bookings)                                   AS bookings,
      (SELECT COUNT(*) FROM bookings WHERE status = 'CONFIRMED')        AS confirmed,
      (SELECT COUNT(*) FROM bookings WHERE status = 'PENDING')          AS pending,
      (SELECT COUNT(*) FROM bookings WHERE status = 'EXPIRED')          AS expired,
      (SELECT COUNT(*) FROM bookings WHERE status = 'CANCELLED')        AS cancelled,
      (SELECT COUNT(*) FROM payments WHERE status = 'REFUND_PENDING')   AS refunds_pending,
      (SELECT COUNT(*) FROM payment_webhook_events)                     AS webhook_events_applied"""


def main() -> int:
    # sync drivers: sqlite+aiosqlite -> sqlite, mysql+aiomysql -> mysql+pymysql
    url = Settings().database_url.replace("+aiosqlite", "").replace("+aiomysql", "+pymysql")
    # same text format the app stores, so the comparison also works on SQLite
    cutoff = (utcnow() - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S.%f")
    engine = create_engine(url)
    failed = False
    with engine.connect() as conn:
        for name, sql in CHECKS.items():
            params = {"cutoff": cutoff} if ":cutoff" in sql else {}
            rows = conn.execute(text(sql), params).fetchall()
            status = "PASS" if not rows else f"FAIL ({len(rows)} rows)"
            print(f"[{status}] {name}")
            for row in rows[:10]:
                print("        ", dict(row._mapping))
            failed |= bool(rows)
        print("\nsummary:", dict(conn.execute(text(SUMMARY)).one()._mapping))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
