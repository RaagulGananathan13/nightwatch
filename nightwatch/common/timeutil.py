"""Time helpers. NightWatch only ever handles timezone-aware UTC times."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Fixed-width UTC text (``2026-10-28T10:54:01.123Z``), so stored timestamps sort correctly."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_iso(text: str) -> datetime:
    """Parse ISO 8601 text, including the ``+0000`` style CloudWatch uses."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
