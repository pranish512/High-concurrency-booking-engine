-- =====================================================================
-- SQLite schema (same design as sql/mysql/01_schema.sql).
--
-- SQLite specifics
--   * ENUMs become TEXT + CHECK (status IN (...)); CHECKs are enforced.
--   * Timestamps are TEXT 'YYYY-MM-DD HH:MM:SS.ffffff' (SQLAlchemy's format),
--     so string comparison == time comparison.
--   * Foreign keys are enforced per connection (the app sets PRAGMA foreign_keys=ON).
--   * There are no row locks: writers are serialized by BEGIN IMMEDIATE, which
--     makes the guarded UPDATE on show_seats race-free.
-- =====================================================================

CREATE TABLE cities (
    city_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    state       TEXT NOT NULL,
    UNIQUE (name, state)
);

CREATE TABLE languages (
    language_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE genres (
    genre_id    INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE users (
    user_id     INTEGER PRIMARY KEY,
    full_name   TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')
);

CREATE TABLE movies (
    movie_id      INTEGER PRIMARY KEY,
    title         TEXT    NOT NULL,
    duration_min  INTEGER NOT NULL CHECK (duration_min > 0),
    certification TEXT    NOT NULL CHECK (certification IN ('U','UA','A','S')),
    release_date  TEXT    NOT NULL,
    UNIQUE (title, release_date)
);

CREATE TABLE movie_genres (
    movie_id    INTEGER NOT NULL REFERENCES movies (movie_id),
    genre_id    INTEGER NOT NULL REFERENCES genres (genre_id),
    PRIMARY KEY (movie_id, genre_id)
);
CREATE INDEX idx_movie_genres_genre ON movie_genres (genre_id);

CREATE TABLE theatres (
    theatre_id  INTEGER PRIMARY KEY,
    city_id     INTEGER NOT NULL REFERENCES cities (city_id),
    name        TEXT    NOT NULL,
    address     TEXT    NOT NULL,
    UNIQUE (city_id, name)
);

CREATE TABLE screens (
    screen_id   INTEGER PRIMARY KEY,
    theatre_id  INTEGER NOT NULL REFERENCES theatres (theatre_id),
    name        TEXT    NOT NULL,
    UNIQUE (theatre_id, name)
);

CREATE TABLE seats (
    seat_id     INTEGER PRIMARY KEY,
    screen_id   INTEGER NOT NULL REFERENCES screens (screen_id),
    row_label   TEXT    NOT NULL,
    seat_number INTEGER NOT NULL,
    seat_type   TEXT    NOT NULL CHECK (seat_type IN ('REGULAR','PREMIUM','RECLINER')),
    UNIQUE (screen_id, row_label, seat_number)
);

CREATE TABLE shows (
    show_id     INTEGER PRIMARY KEY,
    screen_id   INTEGER NOT NULL REFERENCES screens (screen_id),
    movie_id    INTEGER NOT NULL REFERENCES movies (movie_id),
    language_id INTEGER NOT NULL REFERENCES languages (language_id),
    format      TEXT    NOT NULL DEFAULT '2D' CHECK (format IN ('2D','3D','IMAX_2D','IMAX_3D','4DX')),
    start_time  TEXT    NOT NULL,   -- theatre-local
    end_time    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'SCHEDULED' CHECK (status IN ('SCHEDULED','CANCELLED')),
    UNIQUE (screen_id, start_time),
    CHECK (end_time > start_time)
);
CREATE INDEX idx_shows_movie_start ON shows (movie_id, start_time);

CREATE TABLE show_seat_prices (
    show_id     INTEGER NOT NULL REFERENCES shows (show_id),
    seat_type   TEXT    NOT NULL CHECK (seat_type IN ('REGULAR','PREMIUM','RECLINER')),
    price       NUMERIC NOT NULL CHECK (price >= 0),
    PRIMARY KEY (show_id, seat_type)
);

CREATE TABLE bookings (
    booking_id      INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users (user_id),
    show_id         INTEGER NOT NULL REFERENCES shows (show_id),
    status          TEXT    NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','CONFIRMED','EXPIRED','CANCELLED','FAILED')),
    idempotency_key TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    expires_at      TEXT    NOT NULL,   -- hold deadline (UTC)
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX idx_bookings_status_expiry ON bookings (status, expires_at);
CREATE INDEX idx_bookings_show ON bookings (show_id);

-- Seat inventory per show: the row every booking competes for.
CREATE TABLE show_seats (
    show_id     INTEGER NOT NULL REFERENCES shows (show_id),
    seat_id     INTEGER NOT NULL REFERENCES seats (seat_id),
    status      TEXT    NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE','HELD','BOOKED')),
    booking_id  INTEGER REFERENCES bookings (booking_id),
    PRIMARY KEY (show_id, seat_id),
    CHECK ((status = 'AVAILABLE' AND booking_id IS NULL) OR
           (status IN ('HELD','BOOKED') AND booking_id IS NOT NULL))
) WITHOUT ROWID;
CREATE INDEX idx_show_seats_booking ON show_seats (booking_id);
CREATE INDEX idx_show_seats_seat ON show_seats (seat_id);

CREATE TABLE booking_seats (
    booking_id  INTEGER NOT NULL REFERENCES bookings (booking_id),
    seat_id     INTEGER NOT NULL REFERENCES seats (seat_id),
    price       NUMERIC NOT NULL CHECK (price >= 0),
    PRIMARY KEY (booking_id, seat_id)
) WITHOUT ROWID;
CREATE INDEX idx_booking_seats_seat ON booking_seats (seat_id);

CREATE TABLE payments (
    payment_id         INTEGER PRIMARY KEY,
    booking_id         INTEGER NOT NULL REFERENCES bookings (booking_id),
    gateway            TEXT    NOT NULL,
    gateway_order_id   TEXT    NOT NULL UNIQUE,
    gateway_payment_id TEXT    UNIQUE,
    amount             NUMERIC NOT NULL CHECK (amount > 0),
    currency           TEXT    NOT NULL DEFAULT 'INR',
    status             TEXT    NOT NULL DEFAULT 'CREATED'
                       CHECK (status IN ('CREATED','SUCCEEDED','FAILED','REFUND_PENDING','REFUNDED')),
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
CREATE INDEX idx_payments_booking ON payments (booking_id);

-- Idempotency ledger: a redelivered event_id fails its INSERT.
CREATE TABLE payment_webhook_events (
    event_id         TEXT PRIMARY KEY,
    gateway_order_id TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    payload          TEXT NOT NULL CHECK (json_valid(payload)),
    outcome          TEXT,
    received_at      TEXT NOT NULL,
    processed_at     TEXT
) WITHOUT ROWID;
CREATE INDEX idx_webhook_events_order ON payment_webhook_events (gateway_order_id);
