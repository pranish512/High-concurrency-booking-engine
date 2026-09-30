-- =====================================================================
-- Load-test fixtures (optional): 10,000 users and a 500-seat "Mega Screen"
-- show tomorrow at 23:00 at theatre 1, titled "Load Test Premiere".
-- The Locust file discovers the show by that title.
-- =====================================================================
USE bookmyshow;

SET SESSION cte_max_recursion_depth = 20000;

INSERT INTO users (full_name, email, phone)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 10000)
SELECT CONCAT('Load User ', i),
       CONCAT('load.user.', i, '@example.com'),
       CONCAT('7', LPAD(i, 9, '0'))
FROM n;

INSERT INTO movies (title, duration_min, certification, release_date)
VALUES ('Load Test Premiere', 120, 'UA', CURDATE());

INSERT INTO screens (theatre_id, name) VALUES (1, 'Mega Screen');
SET @mega := LAST_INSERT_ID();

-- 20 rows (A-T) x 25 seats = 500 seats
INSERT INTO seats (screen_id, row_label, seat_number, seat_type)
WITH RECURSIVE r (n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM r WHERE n < 19),
               c (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 25)
SELECT @mega,
       CHAR(65 + r.n USING utf8mb4),
       c.n,
       CASE WHEN r.n < 12 THEN 'REGULAR' WHEN r.n < 18 THEN 'PREMIUM' ELSE 'RECLINER' END
FROM r CROSS JOIN c;

INSERT INTO shows (screen_id, movie_id, language_id, format, start_time, end_time)
SELECT @mega, m.movie_id, 1, '2D',
       TIMESTAMP(CURDATE() + INTERVAL 1 DAY, '23:00:00'),
       TIMESTAMP(CURDATE() + INTERVAL 1 DAY, '23:00:00') + INTERVAL 140 MINUTE
FROM movies m
WHERE m.title = 'Load Test Premiere';
SET @lt_show := LAST_INSERT_ID();

INSERT INTO show_seat_prices (show_id, seat_type, price) VALUES
    (@lt_show, 'REGULAR', 200.00), (@lt_show, 'PREMIUM', 320.00), (@lt_show, 'RECLINER', 550.00);

INSERT INTO show_seats (show_id, seat_id)
SELECT @lt_show, seat_id FROM seats WHERE screen_id = @mega;

SELECT @lt_show AS loadtest_show_id;
