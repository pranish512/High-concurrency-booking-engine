-- =====================================================================
-- P1 — sample data. Run on a fresh database right after 01_schema.sql.
-- Show dates are relative to CURDATE() so the "next 7 dates" picker
-- always has data, whenever this is loaded.
-- =====================================================================
USE bookmyshow;

INSERT INTO cities (name, state) VALUES
    ('Mumbai',    'Maharashtra'),
    ('Bengaluru', 'Karnataka');

INSERT INTO languages (name) VALUES ('Hindi'), ('English'), ('Tamil'), ('Telugu');

INSERT INTO genres (name) VALUES ('Action'), ('Drama'), ('Comedy'), ('Sci-Fi'), ('Thriller'), ('Romance');

INSERT INTO users (full_name, email, phone) VALUES
    ('Aarav Mehta',  'aarav.mehta@example.com',  '9810000001'),
    ('Diya Sharma',  'diya.sharma@example.com',  '9810000002'),
    ('Kabir Nair',   'kabir.nair@example.com',   '9810000003');

INSERT INTO movies (title, duration_min, certification, release_date) VALUES
    ('Skyline Heist',      148, 'UA', CURDATE() - INTERVAL 10 DAY),
    ('Monsoon Letters',    132, 'U',  CURDATE() - INTERVAL 3 DAY),
    ('Orbit 9',            155, 'UA', CURDATE() - INTERVAL 17 DAY),
    ('Laugh Riot Express', 118, 'U',  CURDATE());

INSERT INTO movie_genres (movie_id, genre_id) VALUES
    (1, 1), (1, 5),          -- Skyline Heist: Action, Thriller
    (2, 2), (2, 6),          -- Monsoon Letters: Drama, Romance
    (3, 4), (3, 1),          -- Orbit 9: Sci-Fi, Action
    (4, 3);                  -- Laugh Riot Express: Comedy

INSERT INTO theatres (city_id, name, address) VALUES
    (1, 'Cineplex Lower Parel',     'Senapati Bapat Marg, Lower Parel, Mumbai 400013'),
    (1, 'Galaxy Talkies Andheri',   'Link Road, Andheri West, Mumbai 400053'),
    (2, 'Orion Multiplex Koramangala', '80 Feet Road, Koramangala, Bengaluru 560034');

INSERT INTO screens (theatre_id, name) VALUES
    (1, 'Audi 1'), (1, 'Audi 2'),
    (2, 'Screen 1'),
    (3, 'Screen 1');

-- 8 rows (A-H) x 12 seats per screen: A-D regular, E-G premium, H recliner.
INSERT INTO seats (screen_id, row_label, seat_number, seat_type)
WITH RECURSIVE r (n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM r WHERE n < 7),
               c (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 12)
SELECT sc.screen_id,
       CHAR(65 + r.n USING utf8mb4),
       c.n,
       CASE WHEN r.n <= 3 THEN 'REGULAR' WHEN r.n <= 6 THEN 'PREMIUM' ELSE 'RECLINER' END
FROM screens sc
CROSS JOIN r
CROSS JOIN c;

-- Today's detailed schedule (end_time = start + runtime + 20 min for ads/cleaning).
INSERT INTO shows (screen_id, movie_id, language_id, format, start_time, end_time)
SELECT p.screen_id, p.movie_id, p.language_id, p.format,
       TIMESTAMP(CURDATE(), p.start_at),
       TIMESTAMP(CURDATE(), p.start_at) + INTERVAL (m.duration_min + 20) MINUTE
FROM (
              SELECT 1 AS screen_id, 1 AS movie_id, 1 AS language_id, '2D' AS format, '10:00:00' AS start_at
    UNION ALL SELECT 1, 1, 1, '2D',      '13:30:00'
    UNION ALL SELECT 1, 1, 1, '2D',      '17:00:00'
    UNION ALL SELECT 1, 1, 1, '2D',      '20:30:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', '09:30:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', '12:45:00'
    UNION ALL SELECT 2, 2, 1, '2D',      '16:15:00'
    UNION ALL SELECT 2, 2, 1, '2D',      '19:00:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', '22:00:00'
    UNION ALL SELECT 3, 4, 1, '2D',      '11:00:00'
    UNION ALL SELECT 3, 4, 1, '2D',      '14:00:00'
    UNION ALL SELECT 3, 1, 1, '2D',      '18:00:00'
    UNION ALL SELECT 3, 1, 1, '2D',      '21:30:00'
    UNION ALL SELECT 4, 3, 2, '2D',      '19:15:00'
) p
JOIN movies m ON m.movie_id = p.movie_id;

-- Recurring schedule for the next 6 days, so every date in the picker has shows.
INSERT INTO shows (screen_id, movie_id, language_id, format, start_time, end_time)
WITH RECURSIVE d (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM d WHERE n < 6)
SELECT p.screen_id, p.movie_id, p.language_id, p.format,
       TIMESTAMP(CURDATE() + INTERVAL d.n DAY, p.start_at),
       TIMESTAMP(CURDATE() + INTERVAL d.n DAY, p.start_at) + INTERVAL (m.duration_min + 20) MINUTE
FROM (
              SELECT 1 AS screen_id, 1 AS movie_id, 1 AS language_id, '2D' AS format, '10:00:00' AS start_at
    UNION ALL SELECT 1, 1, 1, '2D',      '18:00:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', '13:00:00'
    UNION ALL SELECT 2, 2, 1, '2D',      '20:00:00'
    UNION ALL SELECT 3, 4, 1, '2D',      '15:00:00'
) p
CROSS JOIN d
JOIN movies m ON m.movie_id = p.movie_id;

-- Prices per (show, seat type); IMAX/3D shows cost 100 more.
INSERT INTO show_seat_prices (show_id, seat_type, price)
SELECT s.show_id, t.seat_type,
       t.base_price + CASE WHEN s.format IN ('3D', 'IMAX_2D', 'IMAX_3D', '4DX') THEN 100 ELSE 0 END
FROM shows s
CROSS JOIN (
              SELECT 'REGULAR' AS seat_type, 200.00 AS base_price
    UNION ALL SELECT 'PREMIUM',  320.00
    UNION ALL SELECT 'RECLINER', 550.00
) t;

-- Seat inventory: one row per (show, seat of that show's screen).
INSERT INTO show_seats (show_id, seat_id)
SELECT s.show_id, se.seat_id
FROM shows s
JOIN seats se ON se.screen_id = s.screen_id;

-- ---------------------------------------------------------------------
-- Sample bookings on show 1 (Skyline Heist, Audi 1, today 10:00)
-- ---------------------------------------------------------------------
SET @now := UTC_TIMESTAMP(3);
SET @e5 := (SELECT seat_id FROM seats WHERE screen_id = 1 AND row_label = 'E' AND seat_number = 5);
SET @e6 := (SELECT seat_id FROM seats WHERE screen_id = 1 AND row_label = 'E' AND seat_number = 6);
SET @f1 := (SELECT seat_id FROM seats WHERE screen_id = 1 AND row_label = 'F' AND seat_number = 1);
SET @a1 := (SELECT seat_id FROM seats WHERE screen_id = 1 AND row_label = 'A' AND seat_number = 1);

-- 1) Confirmed: Aarav paid for E5 + E6.
INSERT INTO bookings (user_id, show_id, status, idempotency_key, created_at, updated_at, expires_at)
VALUES (1, 1, 'CONFIRMED', 'seed-booking-1', @now - INTERVAL 20 MINUTE, @now - INTERVAL 18 MINUTE, @now - INTERVAL 15 MINUTE);
INSERT INTO booking_seats (booking_id, seat_id, price) VALUES (1, @e5, 320.00), (1, @e6, 320.00);
UPDATE show_seats SET status = 'BOOKED', booking_id = 1 WHERE show_id = 1 AND seat_id IN (@e5, @e6);
INSERT INTO payments (booking_id, gateway, gateway_order_id, gateway_payment_id, amount, status, created_at, updated_at)
VALUES (1, 'mockpay', 'order_seed_0001', 'pay_seed_0001', 640.00, 'SUCCEEDED', @now - INTERVAL 19 MINUTE, @now - INTERVAL 18 MINUTE);
INSERT INTO payment_webhook_events (event_id, gateway_order_id, event_type, payload, outcome, received_at, processed_at)
VALUES ('evt_seed_0001', 'order_seed_0001', 'payment.succeeded',
        JSON_OBJECT('event_id', 'evt_seed_0001', 'event_type', 'payment.succeeded',
                    'gateway_order_id', 'order_seed_0001', 'gateway_payment_id', 'pay_seed_0001', 'amount', '640.00'),
        'CONFIRMED', @now - INTERVAL 18 MINUTE, @now - INTERVAL 18 MINUTE);

-- 2) Pending: Diya is holding F1 and has started paying (hold lasts 5 minutes).
INSERT INTO bookings (user_id, show_id, status, idempotency_key, created_at, updated_at, expires_at)
VALUES (2, 1, 'PENDING', 'seed-booking-2', @now, @now, @now + INTERVAL 5 MINUTE);
INSERT INTO booking_seats (booking_id, seat_id, price) VALUES (2, @f1, 320.00);
UPDATE show_seats SET status = 'HELD', booking_id = 2 WHERE show_id = 1 AND seat_id = @f1;
INSERT INTO payments (booking_id, gateway, gateway_order_id, amount, status, created_at, updated_at)
VALUES (2, 'mockpay', 'order_seed_0002', 320.00, 'CREATED', @now, @now);

-- 3) Expired: Kabir held A1 but never paid; the seat went back to AVAILABLE,
--    booking_seats keeps the history.
INSERT INTO bookings (user_id, show_id, status, idempotency_key, created_at, updated_at, expires_at)
VALUES (3, 1, 'EXPIRED', 'seed-booking-3', @now - INTERVAL 30 MINUTE, @now - INTERVAL 25 MINUTE, @now - INTERVAL 25 MINUTE);
INSERT INTO booking_seats (booking_id, seat_id, price) VALUES (3, @a1, 200.00);
