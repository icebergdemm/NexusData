"""
Unified timezone utilities for the project.
All times in DB are stored as naive datetime in UTC.
Display everywhere in MSK (UTC+3).
"""

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """Current time in Moscow timezone as naive datetime."""
    return datetime.now(tz=MSK).replace(tzinfo=None)


def utc_to_msk(dt) -> datetime:
    """Convert a naive UTC datetime to naive MSK datetime (UTC+3)."""
    if dt is None:
        return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        # Already aware — convert to MSK
        return dt.astimezone(MSK).replace(tzinfo=None)
    # Assume naive = UTC, add 3 hours
    return dt + timedelta(hours=3)


def format_msk(dt, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Format a UTC datetime as MSK string."""
    if dt is None:
        return "-"
    msk_dt = utc_to_msk(dt)
    return msk_dt.strftime(fmt)
