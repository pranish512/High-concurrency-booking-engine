-- =====================================================================
-- Load-test fixtures: 10,000 users and a 500-seat "Mega Screen" show
-- tomorrow at 23:00 at theatre 1, titled "Load Test Premiere".
-- The Locust file discovers the show by that title.
-- =====================================================================

WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 10000)
INSERT INTO users (full_name, email, phone)
SELECT 'Load User ' || i, 'load.user.' || i || '@example.com', '7' || printf('%09d', i) FROM n;

INSERT INTO movies (title, duration_min, certification, release_date)
VALUES ('Load Test Premiere', 120, 'UA', date('now', 'localtime'));

INSERT INTO screens (theatre_id, name) VALUES (1, 'Mega Screen');

-- 20 rows (A-T) x 25 seats = 500 seats
WITH RECURSIVE r (n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM r WHERE n < 19),
               c (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 25)
INSERT INTO seats (screen_id, row_label, seat_number, seat_type)
SELECT (SELECT screen_id FROM screens WHERE theatre_id = 1 AND name = 'Mega Screen'),
       char(65 + r.n), c.n,
       CASE WHEN r.n < 12 THEN 'REGULAR' WHEN r.n < 18 THEN 'PREMIUM' ELSE 'RECLINER' END
FROM r, c
ORDER BY r.n, c.n;

INSERT INTO shows (screen_id, movie_id, language_id, format, start_time, end_time)
SELECT sc.screen_id, m.movie_id, 1, '2D',
       date('now', 'localtime', '+1 day') || ' 23:00:00.000000',
       datetime(date('now', 'localtime', '+1 day') || ' 23:00:00', '+140 minutes') || '.000000'
FROM screens sc, movies m
WHERE sc.theatre_id = 1 AND sc.name = 'Mega Screen' AND m.title = 'Load Test Premiere';

INSERT INTO show_seat_prices (show_id, seat_type, price)
SELECT s.show_id, t.seat_type, t.price
FROM shows s JOIN movies m ON m.movie_id = s.movie_id,
     (SELECT 'REGULAR' AS seat_type, 200.00 AS price
      UNION ALL SELECT 'PREMIUM', 320.00
      UNION ALL SELECT 'RECLINER', 550.00) t
WHERE m.title = 'Load Test Premiere';

INSERT INTO show_seats (show_id, seat_id)
SELECT s.show_id, se.seat_id
FROM shows s JOIN movies m ON m.movie_id = s.movie_id
JOIN seats se ON se.screen_id = s.screen_id
WHERE m.title = 'Load Test Premiere';
