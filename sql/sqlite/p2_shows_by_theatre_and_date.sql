-- =====================================================================
-- P2 (SQLite) — all shows on a given date at a given theatre, with timings.
-- Edit the `params` row and run:  sqlite3 data/booking.db < this_file
-- =====================================================================

-- (a) one row per show
WITH params AS (SELECT 1 AS theatre_id, date('now', 'localtime') AS show_date)
SELECT  m.movie_id,
        m.title,
        m.certification,
        l.name                              AS language,
        s.format,
        sc.name                             AS screen,
        s.show_id,
        strftime('%H:%M', s.start_time)     AS show_time
FROM    params p
JOIN    screens   sc ON sc.theatre_id  = p.theatre_id
JOIN    shows     s  ON s.screen_id    = sc.screen_id
JOIN    movies    m  ON m.movie_id     = s.movie_id
JOIN    languages l  ON l.language_id  = s.language_id
WHERE   s.start_time >= p.show_date
  AND   s.start_time <  date(p.show_date, '+1 day')
  AND   s.status = 'SCHEDULED'
ORDER BY m.title, l.name, s.format, s.start_time;

-- (b) grouped like the app screen: one row per movie/language/format
WITH params AS (SELECT 1 AS theatre_id, date('now', 'localtime') AS show_date),
     day_shows AS (
        SELECT m.movie_id, m.title, m.certification, l.name AS language, s.format, s.start_time
        FROM   params p
        JOIN   screens   sc ON sc.theatre_id = p.theatre_id
        JOIN   shows     s  ON s.screen_id   = sc.screen_id
        JOIN   movies    m  ON m.movie_id    = s.movie_id
        JOIN   languages l  ON l.language_id = s.language_id
        WHERE  s.start_time >= p.show_date
          AND  s.start_time <  date(p.show_date, '+1 day')
          AND  s.status = 'SCHEDULED'
        ORDER BY s.start_time
     )
SELECT  title, certification, language, format,
        group_concat(strftime('%H:%M', start_time), ', ') AS show_timings,
        count(*) AS shows
FROM    day_shows
GROUP BY movie_id, title, certification, language, format
ORDER BY title, language, format;

-- (c) the 7-date picker with a show count per date
WITH RECURSIVE picker (d) AS (
    SELECT date('now', 'localtime')
    UNION ALL
    SELECT date(d, '+1 day') FROM picker WHERE d < date('now', 'localtime', '+6 days')
)
SELECT  p.d AS show_date,
        count(s.show_id) AS shows
FROM    picker p
LEFT JOIN screens sc ON sc.theatre_id = 1
LEFT JOIN shows   s  ON s.screen_id   = sc.screen_id
                    AND s.status      = 'SCHEDULED'
                    AND s.start_time >= p.d
                    AND s.start_time <  date(p.d, '+1 day')
GROUP BY p.d
ORDER BY p.d;
