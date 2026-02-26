from datetime import datetime
from zoneinfo import ZoneInfo

from backend.utils.publish_schedule import next_publish_time


def test_next_publish_time_defaults_to_la_sunday_5pm_dst_aware():
    # Summer (PDT, UTC-7)
    now_utc = datetime(2026, 7, 10, 12, 0, tzinfo=ZoneInfo("UTC"))  # Friday
    target = next_publish_time(now_utc=now_utc)
    local = target.astimezone(ZoneInfo("America/Los_Angeles"))

    assert local.weekday() == 6  # Sunday
    assert local.hour == 17
    assert local.minute == 0


def test_same_day_after_cutoff_rolls_to_next_week():
    # Sunday after 5pm local should roll to next Sunday
    now_local = datetime(2026, 6, 14, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    now_utc = now_local.astimezone(ZoneInfo("UTC"))

    target = next_publish_time(now_utc=now_utc)
    local = target.astimezone(ZoneInfo("America/Los_Angeles"))

    assert local.weekday() == 6
    assert local.day == 21
    assert local.hour == 17
