"""Apply the SQLite migrations (and optionally seed data).

    python -m app.migrate            # schema only
    python -m app.migrate --seed     # schema + sample data + load-test data
    python -m app.migrate --reset    # delete the database file first

Each file runs in its own transaction and is recorded in schema_migrations,
so re-running only applies what is new.
"""

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "sql" / "sqlite" / "migrations"
SEED_DIR = ROOT / "sql" / "sqlite" / "seed"


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise SystemExit(f"migrations target a SQLite file database, got {database_url!r}")
    return Path(url.database)


def apply_migrations(db_path: Path, *, seed: bool = False) -> list[str]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if seed:
        files += sorted(SEED_DIR.glob("*.sql"))

    conn = sqlite3.connect(db_path, isolation_level=None)  # explicit BEGIN/COMMIT below
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')))"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        applied = []
        for path in files:
            version = f"{path.parent.name}/{path.name}"
            if version in done:
                continue
            try:
                conn.executescript(
                    f"BEGIN;\n{path.read_text(encoding='utf-8')}\n"
                    f"INSERT INTO schema_migrations (version) VALUES ('{version}');\nCOMMIT;"
                )
            except sqlite3.Error as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise SystemExit(f"migration {version} failed: {exc}") from exc
            applied.append(version)
        return applied
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true", help="also load sample and load-test data")
    parser.add_argument("--reset", action="store_true", help="delete the database file before migrating")
    args = parser.parse_args()

    db_path = sqlite_path(Settings().database_url)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        print(f"removed {db_path}")

    applied = apply_migrations(db_path, seed=args.seed)
    for version in applied:
        print(f"applied {version}")
    print(f"{db_path}: up to date ({len(applied)} new)")


if __name__ == "__main__":
    main()
