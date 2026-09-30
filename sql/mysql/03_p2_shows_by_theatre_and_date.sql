-- =====================================================================
-- P2 — all shows on a given date at a given theatre, with show timings.
-- Change the two variables and run. Directly executable on MySQL 8.
-- =====================================================================
USE bookmyshow;

SET @theatre_id := 1;
SET @show_date  := CURDATE();          -- e.g. '2026-10-01'

-- ---------------------------------------------------------------------
-- P2 (a): one row per show.
-- The date filter is a half-open range on the raw column (not DATE(start_time)),
-- so it can use the (screen_id, start_time) unique index for each screen.
-- ---------------------------------------------------------------------
SELECT  m.movie_id,
        m.title,
        m.certification,
        m.duration_min,
        l.name                                  AS language,
        s.format,
        sc.name                                 AS screen,
        s.show_id,
        s.start_time,
        TIME_FORMAT(s.start_time, '%h:%i %p')   AS show_time
FROM    screens   sc
JOIN    shows     s  ON s.screen_id   = sc.screen_id
JOIN    movies    m  ON m.movie_id    = s.movie_id
JOIN    languages l  ON l.language_id = s.language_id
WHERE   sc.theatre_id = @theatre_id
  AND   s.start_time >= @show_date
  AND   s.start_time <  @show_date + INTERVAL 1 DAY
  AND   s.status = 'SCHEDULED'
ORDER BY m.title, l.name, s.format, s.start_time;

-- ---------------------------------------------------------------------
-- P2 (b): same data shaped like the app screen — one row per movie
-- (language + format), with all its timings in order.
-- ---------------------------------------------------------------------
SELECT  m.title,
        m.certification,
        l.name  AS language,
        s.format,
        GROUP_CONCAT(TIME_FORMAT(s.start_time, '%h:%i %p')
                     ORDER BY s.start_time SEPARATOR ', ') AS show_timings,
        COUNT(*) AS shows
FROM    screens   sc
JOIN    shows     s  ON s.screen_id   = sc.screen_id
JOIN    movies    m  ON m.movie_id    = s.movie_id
JOIN    languages l  ON l.language_id = s.language_id
WHERE   sc.theatre_id = @theatre_id
  AND   s.start_time >= @show_date
  AND   s.start_time <  @show_date + INTERVAL 1 DAY
  AND   s.status = 'SCHEDULED'
GROUP BY m.movie_id, m.title, m.certification, l.name, s.format
ORDER BY m.title, l.name, s.format;

-- ---------------------------------------------------------------------
-- Supporting query: the 7-date picker (today + 6), with a show count per
-- date so the UI can grey out dates that have nothing scheduled.
-- ---------------------------------------------------------------------
WITH RECURSIVE picker (d) AS (
    SELECT CURDATE()
    UNION ALL
    SELECT d + INTERVAL 1 DAY FROM picker WHERE d < CURDATE() + INTERVAL 6 DAY
)
SELECT  p.d AS show_date,
        DATE_FORMAT(p.d, '%a %d %b') AS label,
        COUNT(s.show_id) AS shows
FROM    picker p
LEFT JOIN screens sc ON sc.theatre_id = @theatre_id
LEFT JOIN shows   s  ON s.screen_id   = sc.screen_id
                    AND s.status      = 'SCHEDULED'
                    AND s.start_time >= p.d
                    AND s.start_time <  p.d + INTERVAL 1 DAY
GROUP BY p.d
ORDER BY p.d;

-- ---------------------------------------------------------------------
-- Supporting query: P2 plus seats left per show (the green/amber
-- timing colours in the app). Uses the show_seats PK prefix (show_id).
-- ---------------------------------------------------------------------
SELECT  m.title,
        l.name AS language,
        s.format,
        s.show_id,
        TIME_FORMAT(s.start_time, '%h:%i %p') AS show_time,
        SUM(ss.status = 'AVAILABLE')          AS seats_available,
        COUNT(ss.seat_id)                     AS seats_total
FROM    screens    sc
JOIN    shows      s  ON s.screen_id   = sc.screen_id
JOIN    movies     m  ON m.movie_id    = s.movie_id
JOIN    languages  l  ON l.language_id = s.language_id
JOIN    show_seats ss ON ss.show_id    = s.show_id
WHERE   sc.theatre_id = @theatre_id
  AND   s.start_time >= @show_date
  AND   s.start_time <  @show_date + INTERVAL 1 DAY
  AND   s.status = 'SCHEDULED'
GROUP BY s.show_id, m.title, l.name, s.format, s.start_time
ORDER BY m.title, s.start_time;
