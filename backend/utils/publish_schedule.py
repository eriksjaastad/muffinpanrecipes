"""Publish scheduling utilities (DST-aware)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PUBLISH_TIMEZONE = os.getenv("MUFFINPAN_PUBLISH_TIMEZONE", "America/Los_Angeles")
PUBLISH_WEEKDAY = int(os.getenv("MUFFINPAN_PUBLISH_WEEKDAY", "6"))  # 0=Mon ... 6=Sun
PUBLISH_HOUR = int(os.getenv("MUFFINPAN_PUBLISH_HOUR", "17"))
PUBLISH_MINUTE = int(os.getenv("MUFFINPAN_PUBLISH_MINUTE", "0"))


def next_publish_time(
    now_utc: datetime | None = None,
    timezone_name: str = PUBLISH_TIMEZONE,
    weekday: int = PUBLISH_WEEKDAY,
    hour: int = PUBLISH_HOUR,
    minute: int = PUBLISH_MINUTE,
) -> datetime:
    """Return next scheduled publish time in UTC.

    Defaults to Sunday 17:00 in America/Los_Angeles and is DST-aware.
    """
    tz = ZoneInfo(timezone_name)
    now_utc = now_utc or datetime.now(tz=ZoneInfo("UTC"))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=ZoneInfo("UTC"))

    local_now = now_utc.astimezone(tz)
    target_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    days_ahead = (weekday - local_now.weekday()) % 7
    if days_ahead == 0 and local_now >= target_local:
        days_ahead = 7

    target_local = target_local + timedelta(days=days_ahead)
    return target_local.astimezone(ZoneInfo("UTC"))
