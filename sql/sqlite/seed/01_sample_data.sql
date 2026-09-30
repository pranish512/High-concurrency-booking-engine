-- =====================================================================
-- Sample data (same rows as sql/mysql/02_seed.sql). Show dates are relative
-- to today in local time, so the 7-date picker always has shows.
-- Timestamps use the 'YYYY-MM-DD HH:MM:SS.ffffff' format the app writes.
-- =====================================================================

INSERT INTO cities (city_id, name, state) VALUES
    (1, 'Mumbai',    'Maharashtra'),
    (2, 'Bengaluru', 'Karnataka');

INSERT INTO languages (language_id, name) VALUES (1, 'Hindi'), (2, 'English'), (3, 'Tamil'), (4, 'Telugu');

INSERT INTO genres (genre_id, name) VALUES
    (1, 'Action'), (2, 'Drama'), (3, 'Comedy'), (4, 'Sci-Fi'), (5, 'Thriller'), (6, 'Romance');

INSERT INTO users (user_id, full_name, email, phone) VALUES
    (1, 'Aarav Mehta', 'aarav.mehta@example.com', '9810000001'),
    (2, 'Diya Sharma', 'diya.sharma@example.com', '9810000002'),
    (3, 'Kabir Nair',  'kabir.nair@example.com',  '9810000003');

INSERT INTO movies (movie_id, title, duration_min, certification, release_date) VALUES
    (1, 'Skyline Heist',      148, 'UA', date('now', 'localtime', '-10 days')),
    (2, 'Monsoon Letters',    132, 'U',  date('now', 'localtime', '-3 days')),
    (3, 'Orbit 9',            155, 'UA', date('now', 'localtime', '-17 days')),
    (4, 'Laugh Riot Express', 118, 'U',  date('now', 'localtime'));

INSERT INTO movie_genres (movie_id, genre_id) VALUES (1, 1), (1, 5), (2, 2), (2, 6), (3, 4), (3, 1), (4, 3);

INSERT INTO theatres (theatre_id, city_id, name, address) VALUES
    (1, 1, 'Cineplex Lower Parel',        'Senapati Bapat Marg, Lower Parel, Mumbai 400013'),
    (2, 1, 'Galaxy Talkies Andheri',      'Link Road, Andheri West, Mumbai 400053'),
    (3, 2, 'Orion Multiplex Koramangala', '80 Feet Road, Koramangala, Bengaluru 560034');

INSERT INTO screens (screen_id, theatre_id, name) VALUES
    (1, 1, 'Audi 1'), (2, 1, 'Audi 2'), (3, 2, 'Screen 1'), (4, 3, 'Screen 1');

-- 8 rows (A-H) x 12 seats per screen: A-D regular, E-G premium, H recliner.
WITH RECURSIVE r (n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM r WHERE n < 7),
               c (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 12)
INSERT INTO seats (screen_id, row_label, seat_number, seat_type)
SELECT sc.screen_id, char(65 + r.n), c.n,
       CASE WHEN r.n <= 3 THEN 'REGULAR' WHEN r.n <= 6 THEN 'PREMIUM' ELSE 'RECLINER' END
FROM screens sc, r, c
ORDER BY sc.screen_id, r.n, c.n;

-- Today's detailed schedule, then a recurring schedule for the next 6 days.
-- end_time = start + runtime + 20 min (ads/cleaning).
WITH plan (screen_id, movie_id, language_id, format, day_offset, start_at) AS (
              SELECT 1, 1, 1, '2D',      0, '10:00:00'
    UNION ALL SELECT 1, 1, 1, '2D',      0, '13:30:00'
    UNION ALL SELECT 1, 1, 1, '2D',      0, '17:00:00'
    UNION ALL SELECT 1, 1, 1, '2D',      0, '20:30:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', 0, '09:30:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', 0, '12:45:00'
    UNION ALL SELECT 2, 2, 1, '2D',      0, '16:15:00'
    UNION ALL SELECT 2, 2, 1, '2D',      0, '19:00:00'
    UNION ALL SELECT 2, 3, 2, 'IMAX_3D', 0, '22:00:00'
    UNION ALL SELECT 3, 4, 1, '2D',      0, '11:00:00'
    UNION ALL SELECT 3, 4, 1, '2D',      0, '14:00:00'
    UNION ALL SELECT 3, 1, 1, '2D',      0, '18:00:00'
    UNION ALL SELECT 3, 1, 1, '2D',      0, '21:30:00'
    UNION ALL SELECT 4, 3, 2, '2D',      0, '19:15:00'
),
days (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM days WHERE n < 6),
recurring (screen_id, movie_id, language_id, format, day_offset, start_at) AS (
    SELECT p.screen_id, p.movie_id, p.language_id, p.format, d.n, p.start_at
    FROM (          SELECT 1 AS screen_id, 1 AS movie_id, 1 AS language_id, '2D' AS format, '10:00:00' AS start_at
          UNION ALL SELECT 1, 1, 1, '2D',      '18:00:00'
          UNION ALL SELECT 2, 3, 2, 'IMAX_3D', '13:00:00'
          UNION ALL SELECT 2, 2, 1, '2D',      '20:00:00'
          UNION ALL SELECT 3, 4, 1, '2D',      '15:00:00') p, days d
),
schedule AS (
    SELECT *, date('now', 'localtime', '+' || day_offset || ' days') || ' ' || start_at AS starts
    FROM (SELECT * FROM plan UNION ALL SELECT * FROM recurring)
)
INSERT INTO shows (screen_id, movie_id, language_id, format, start_time, end_time)
SELECT s.screen_id, s.movie_id, s.language_id, s.format,
       s.starts || '.000000',
       datetime(s.starts, '+' || (m.duration_min + 20) || ' minutes') || '.000000'
FROM schedule s JOIN movies m ON m.movie_id = s.movie_id
ORDER BY s.day_offset, s.screen_id, s.start_at;

-- Prices per (show, seat type); IMAX/3D shows cost 100 more.
INSERT INTO show_seat_prices (show_id, seat_type, price)
SELECT s.show_id, t.seat_type,
       t.base_price + CASE WHEN s.format IN ('3D', 'IMAX_2D', 'IMAX_3D', '4DX') THEN 100 ELSE 0 END
FROM shows s,
     (SELECT 'REGULAR' AS seat_type, 200.00 AS base_price
      UNION ALL SELECT 'PREMIUM', 320.00
      UNION ALL SELECT 'RECLINER', 550.00) t;

-- Seat inventory: one row per (show, seat of that show's screen).
INSERT INTO show_seats (show_id, seat_id)
SELECT s.show_id, se.seat_id FROM shows s JOIN seats se ON se.screen_id = s.screen_id;

-- ---------------------------------------------------------------------
-- Sample bookings on show 1 (Skyline Heist, Audi 1, today 10:00)
-- ---------------------------------------------------------------------

-- 1) Confirmed: Aarav paid for E5 + E6.
INSERT INTO bookings (booking_id, user_id, show_id, status, idempotency_key, created_at, updated_at, expires_at) VALUES
    (1, 1, 1, 'CONFIRMED', 'seed-booking-1',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-20 minutes') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-18 minutes') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-15 minutes') || '000');
INSERT INTO booking_seats (booking_id, seat_id, price)
SELECT 1, seat_id, 320.00 FROM seats WHERE screen_id = 1 AND row_label = 'E' AND seat_number IN (5, 6);
UPDATE show_seats SET status = 'BOOKED', booking_id = 1
WHERE show_id = 1 AND seat_id IN (SELECT seat_id FROM booking_seats WHERE booking_id = 1);
INSERT INTO payments (payment_id, booking_id, gateway, gateway_order_id, gateway_payment_id, amount, status, created_at, updated_at) VALUES
    (1, 1, 'mockpay', 'order_seed_0001', 'pay_seed_0001', 640.00, 'SUCCEEDED',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-19 minutes') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-18 minutes') || '000');
INSERT INTO payment_webhook_events (event_id, gateway_order_id, event_type, payload, outcome, received_at, processed_at) VALUES
    ('evt_seed_0001', 'order_seed_0001', 'payment.succeeded',
     json_object('event_id', 'evt_seed_0001', 'event_type', 'payment.succeeded',
                 'gateway_order_id', 'order_seed_0001', 'gateway_payment_id', 'pay_seed_0001', 'amount', '640.00'),
     'CONFIRMED',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-18 minutes') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-18 minutes') || '000');

-- 2) Pending: Diya is holding F1 and has started paying (hold lasts 5 minutes).
INSERT INTO bookings (booking_id, user_id, show_id, status, idempotency_key, created_at, updated_at, expires_at) VALUES
    (2, 2, 1, 'PENDING', 'seed-booking-2',
     strftime('%Y-%m-%d %H:%M:%f', 'now') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '+5 minutes') || '000');
INSERT INTO booking_seats (booking_id, seat_id, price)
SELECT 2, seat_id, 320.00 FROM seats WHERE screen_id = 1 AND row_label = 'F' AND seat_number = 1;
UPDATE show_seats SET status = 'HELD', booking_id = 2
WHERE show_id = 1 AND seat_id IN (SELECT seat_id FROM booking_seats WHERE booking_id = 2);
INSERT INTO payments (payment_id, booking_id, gateway, gateway_order_id, amount, status, created_at, updated_at) VALUES
    (2, 2, 'mockpay', 'order_seed_0002', 320.00, 'CREATED',
     strftime('%Y-%m-%d %H:%M:%f', 'now') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now') || '000');

-- 3) Expired: Kabir held A1 but never paid; the seat is AVAILABLE again,
--    booking_seats keeps the history.
INSERT INTO bookings (booking_id, user_id, show_id, status, idempotency_key, created_at, updated_at, expires_at) VALUES
    (3, 3, 1, 'EXPIRED', 'seed-booking-3',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-30 minutes') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-25 minutes') || '000',
     strftime('%Y-%m-%d %H:%M:%f', 'now', '-25 minutes') || '000');
INSERT INTO booking_seats (booking_id, seat_id, price)
SELECT 3, seat_id, 200.00 FROM seats WHERE screen_id = 1 AND row_label = 'A' AND seat_number = 1;
