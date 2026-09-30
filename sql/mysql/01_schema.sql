-- =====================================================================
-- P1 — BookMyShow-style booking engine: schema
-- Target: MySQL 8.0.16+ (InnoDB, utf8mb4). CHECK constraints are enforced
-- from 8.0.16 onwards.
--
-- Conventions
--   * Audit timestamps (created_at, expires_at, ...) are UTC.
--   * Show start/end times are theatre-local wall-clock times.
--   * Every table is in BCNF — see docs/SCHEMA.md for the FD analysis.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS bookmyshow
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE bookmyshow;

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cities (
    city_id     INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    name        VARCHAR(100)  NOT NULL,
    state       VARCHAR(100)  NOT NULL,
    PRIMARY KEY (city_id),
    UNIQUE KEY uq_cities_name_state (name, state)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS languages (
    language_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name        VARCHAR(50)       NOT NULL,
    PRIMARY KEY (language_id),
    UNIQUE KEY uq_languages_name (name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS genres (
    genre_id    SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name        VARCHAR(50)       NOT NULL,
    PRIMARY KEY (genre_id),
    UNIQUE KEY uq_genres_name (name)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Users
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    full_name   VARCHAR(100)    NOT NULL,
    email       VARCHAR(255)    NOT NULL,
    phone       VARCHAR(15)     NOT NULL,
    created_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_email (email),
    UNIQUE KEY uq_users_phone (phone)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Movies (a movie has many genres -> junction table keeps 1NF)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS movies (
    movie_id      INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    title         VARCHAR(200)      NOT NULL,
    duration_min  SMALLINT UNSIGNED NOT NULL,
    certification ENUM('U','UA','A','S') NOT NULL,
    release_date  DATE              NOT NULL,
    PRIMARY KEY (movie_id),
    UNIQUE KEY uq_movies_title_release (title, release_date),
    CONSTRAINT chk_movies_duration CHECK (duration_min > 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS movie_genres (
    movie_id    INT UNSIGNED      NOT NULL,
    genre_id    SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (movie_id, genre_id),
    KEY idx_movie_genres_genre (genre_id),
    CONSTRAINT fk_movie_genres_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id),
    CONSTRAINT fk_movie_genres_genre FOREIGN KEY (genre_id) REFERENCES genres (genre_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Venue: theatre -> screens -> physical seats
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS theatres (
    theatre_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    city_id     INT UNSIGNED NOT NULL,
    name        VARCHAR(150) NOT NULL,
    address     VARCHAR(255) NOT NULL,
    PRIMARY KEY (theatre_id),
    UNIQUE KEY uq_theatres_city_name (city_id, name),
    CONSTRAINT fk_theatres_city FOREIGN KEY (city_id) REFERENCES cities (city_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS screens (
    screen_id   INT UNSIGNED NOT NULL AUTO_INCREMENT,
    theatre_id  INT UNSIGNED NOT NULL,
    name        VARCHAR(50)  NOT NULL,
    PRIMARY KEY (screen_id),
    -- also serves "all screens of a theatre" lookups (P2)
    UNIQUE KEY uq_screens_theatre_name (theatre_id, name),
    CONSTRAINT fk_screens_theatre FOREIGN KEY (theatre_id) REFERENCES theatres (theatre_id)
) ENGINE=InnoDB;

-- A seat is a physical chair in a screen. It has NO status column:
-- availability is per show and lives in show_seats.
CREATE TABLE IF NOT EXISTS seats (
    seat_id     BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    screen_id   INT UNSIGNED      NOT NULL,
    row_label   VARCHAR(3)        NOT NULL,
    seat_number SMALLINT UNSIGNED NOT NULL,
    seat_type   ENUM('REGULAR','PREMIUM','RECLINER') NOT NULL,
    PRIMARY KEY (seat_id),
    UNIQUE KEY uq_seats_position (screen_id, row_label, seat_number),
    CONSTRAINT fk_seats_screen FOREIGN KEY (screen_id) REFERENCES screens (screen_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Shows and per-show pricing
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shows (
    show_id     BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    screen_id   INT UNSIGNED      NOT NULL,
    movie_id    INT UNSIGNED      NOT NULL,
    language_id SMALLINT UNSIGNED NOT NULL,
    format      ENUM('2D','3D','IMAX_2D','IMAX_3D','4DX') NOT NULL DEFAULT '2D',
    start_time  DATETIME          NOT NULL,   -- theatre-local
    end_time    DATETIME          NOT NULL,   -- incl. ads/interval/cleaning, set by the scheduler
    status      ENUM('SCHEDULED','CANCELLED') NOT NULL DEFAULT 'SCHEDULED',
    PRIMARY KEY (show_id),
    -- one show per screen per start time; also the P2 access path (screen_id, start_time range)
    UNIQUE KEY uq_shows_screen_start (screen_id, start_time),
    KEY idx_shows_movie_start (movie_id, start_time),
    CONSTRAINT fk_shows_screen   FOREIGN KEY (screen_id)   REFERENCES screens (screen_id),
    CONSTRAINT fk_shows_movie    FOREIGN KEY (movie_id)    REFERENCES movies (movie_id),
    CONSTRAINT fk_shows_language FOREIGN KEY (language_id) REFERENCES languages (language_id),
    CONSTRAINT chk_shows_times CHECK (end_time > start_time)
) ENGINE=InnoDB;

-- Price depends on (show, seat_type), not on the individual seat.
CREATE TABLE IF NOT EXISTS show_seat_prices (
    show_id     BIGINT UNSIGNED NOT NULL,
    seat_type   ENUM('REGULAR','PREMIUM','RECLINER') NOT NULL,
    price       DECIMAL(10,2)   NOT NULL,
    PRIMARY KEY (show_id, seat_type),
    CONSTRAINT fk_show_seat_prices_show FOREIGN KEY (show_id) REFERENCES shows (show_id),
    CONSTRAINT chk_show_seat_prices_price CHECK (price >= 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Bookings
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bookings (
    booking_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id         BIGINT UNSIGNED NOT NULL,
    show_id         BIGINT UNSIGNED NOT NULL,
    status          ENUM('PENDING','CONFIRMED','EXPIRED','CANCELLED','FAILED') NOT NULL DEFAULT 'PENDING',
    idempotency_key VARCHAR(64)     NULL,      -- client-supplied, makes "hold seats" retry-safe
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    expires_at      DATETIME(3)     NOT NULL,  -- hold deadline (UTC)
    PRIMARY KEY (booking_id),
    UNIQUE KEY uq_bookings_idempotency (user_id, idempotency_key),
    KEY idx_bookings_status_expiry (status, expires_at),   -- hold sweeper
    KEY idx_bookings_show (show_id),
    CONSTRAINT fk_bookings_user FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_bookings_show FOREIGN KEY (show_id) REFERENCES shows (show_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Seat inventory per show: THE row that gets locked.
-- The PK (show_id, seat_id) makes it physically impossible for one seat of
-- one show to exist twice, and every state change is a guarded UPDATE:
--   UPDATE show_seats SET status='HELD', booking_id=?
--    WHERE show_id=? AND seat_id IN (...) AND status='AVAILABLE';
-- If affected rows != requested seats, the transaction rolls back.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS show_seats (
    show_id     BIGINT UNSIGNED NOT NULL,
    seat_id     BIGINT UNSIGNED NOT NULL,
    status      ENUM('AVAILABLE','HELD','BOOKED') NOT NULL DEFAULT 'AVAILABLE',
    booking_id  BIGINT UNSIGNED NULL,          -- current holder / owner
    PRIMARY KEY (show_id, seat_id),
    KEY idx_show_seats_booking (booking_id),
    KEY idx_show_seats_seat (seat_id),
    CONSTRAINT fk_show_seats_show    FOREIGN KEY (show_id)    REFERENCES shows (show_id),
    CONSTRAINT fk_show_seats_seat    FOREIGN KEY (seat_id)    REFERENCES seats (seat_id),
    CONSTRAINT fk_show_seats_booking FOREIGN KEY (booking_id) REFERENCES bookings (booking_id),
    CONSTRAINT chk_show_seats_owner CHECK (
        (status = 'AVAILABLE' AND booking_id IS NULL) OR
        (status IN ('HELD','BOOKED') AND booking_id IS NOT NULL)
    )
) ENGINE=InnoDB;

-- What a booking asked for, with the price snapshot at booking time.
-- Kept after a hold expires, so history is never lost.
CREATE TABLE IF NOT EXISTS booking_seats (
    booking_id  BIGINT UNSIGNED NOT NULL,
    seat_id     BIGINT UNSIGNED NOT NULL,
    price       DECIMAL(10,2)   NOT NULL,
    PRIMARY KEY (booking_id, seat_id),
    KEY idx_booking_seats_seat (seat_id),
    CONSTRAINT fk_booking_seats_booking FOREIGN KEY (booking_id) REFERENCES bookings (booking_id),
    CONSTRAINT fk_booking_seats_seat    FOREIGN KEY (seat_id)    REFERENCES seats (seat_id),
    CONSTRAINT chk_booking_seats_price CHECK (price >= 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Payments (one booking may have several attempts)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    payment_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    booking_id         BIGINT UNSIGNED NOT NULL,
    gateway            VARCHAR(30)     NOT NULL,
    gateway_order_id   VARCHAR(64)     NOT NULL,   -- we create it; webhooks reference it
    gateway_payment_id VARCHAR(64)     NULL,       -- gateway's id, known after capture
    amount             DECIMAL(10,2)   NOT NULL,
    currency           CHAR(3)         NOT NULL DEFAULT 'INR',
    status             ENUM('CREATED','SUCCEEDED','FAILED','REFUND_PENDING','REFUNDED') NOT NULL DEFAULT 'CREATED',
    created_at         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (payment_id),
    UNIQUE KEY uq_payments_order (gateway_order_id),
    UNIQUE KEY uq_payments_gateway_payment (gateway_payment_id),
    KEY idx_payments_booking (booking_id),
    CONSTRAINT fk_payments_booking FOREIGN KEY (booking_id) REFERENCES bookings (booking_id),
    CONSTRAINT chk_payments_amount CHECK (amount > 0)
) ENGINE=InnoDB;

-- Idempotency ledger for gateway webhooks: the PK on event_id means a
-- redelivered event fails its INSERT and is acknowledged without side effects.
CREATE TABLE IF NOT EXISTS payment_webhook_events (
    event_id         VARCHAR(64) NOT NULL,
    gateway_order_id VARCHAR(64) NOT NULL,
    event_type       VARCHAR(40) NOT NULL,
    payload          JSON        NOT NULL,
    outcome          VARCHAR(40) NULL,
    received_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    processed_at     DATETIME(3) NULL,
    PRIMARY KEY (event_id),
    KEY idx_webhook_events_order (gateway_order_id)
) ENGINE=InnoDB;
