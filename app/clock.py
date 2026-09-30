from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC, matching the DATETIME(3) audit columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    """Tag a naive-UTC value from the database so the API emits an explicit offset."""
    return value.replace(tzinfo=UTC)
