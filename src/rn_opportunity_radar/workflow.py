from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")
PACIFIC_SCHEDULES = {
    -7: "7 13 * * *",
    -8: "7 14 * * *",
}


def should_run_schedule(schedule: str, at: datetime | None = None) -> bool:
    if not schedule:
        return False

    current = at.astimezone(PACIFIC) if at else datetime.now(PACIFIC)
    offset = current.utcoffset()
    if offset is None:
        return False

    offset_hours = int(offset.total_seconds() // 3600)
    expected_schedule = PACIFIC_SCHEDULES.get(offset_hours)
    return bool(expected_schedule and schedule == expected_schedule)
