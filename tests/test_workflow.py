from datetime import datetime
from zoneinfo import ZoneInfo

from rn_opportunity_radar.workflow import should_run_schedule


def test_should_run_schedule_matches_dst_trigger() -> None:
    at = datetime(2026, 7, 1, 6, 7, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert should_run_schedule("7 13 * * *", at=at) is True


def test_should_run_schedule_matches_standard_time_trigger() -> None:
    at = datetime(2026, 12, 1, 6, 7, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert should_run_schedule("7 14 * * *", at=at) is True


def test_should_run_schedule_rejects_wrong_trigger() -> None:
    at = datetime(2026, 12, 1, 6, 7, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert should_run_schedule("7 13 * * *", at=at) is False
