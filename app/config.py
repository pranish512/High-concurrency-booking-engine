from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default (WAL, single writer per process). A mysql+aiomysql:// URL also works.
    database_url: str = "sqlite+aiosqlite:///./data/booking.db"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # Read pool per process (SQLite always uses a single writer connection).
    db_pool_size: int = 20
    db_max_overflow: int = 10

    hold_ttl_seconds: int = 300
    max_seats_per_booking: int = 10

    # Load shedding: max hold transactions in flight per process, and how long a
    # request may wait for a slot before getting 503 + Retry-After.
    max_concurrent_hold_txns: int = 40
    admission_timeout_seconds: float = 2.0

    sweeper_interval_seconds: float = 2.0
    sweeper_batch_size: int = 200

    webhook_secret: str = "dev-webhook-secret"
    webhook_stream: str = "payments:webhooks"
    webhook_group: str = "payment-workers"

    showtimes_cache_ttl_seconds: int = 30

    # Background workers (hold sweeper, webhook consumer) run inside each API process.
    run_workers: bool = True
    # Exposes POST /dev/gateway/simulate for manual testing. Disable in production.
    enable_dev_gateway: bool = True
