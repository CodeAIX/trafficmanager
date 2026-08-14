from datetime import datetime, time, timezone
from backend.app.calendar import cycle_key, monthly_occurrence, next_occurrence


def test_last_day_and_skip():
    assert monthly_occurrence(2025, 2, 31, time(1), "UTC").day == 28
    assert monthly_occurrence(2024, 2, 31, time(1), "UTC").day == 29
    assert monthly_occurrence(2025, 2, 31, time(1), "UTC", "SKIP") is None


def test_dst_spring_moves_to_first_valid_time():
    result = monthly_occurrence(2026, 3, 8, time(2, 30), "America/New_York")
    local = result.astimezone(__import__('zoneinfo').ZoneInfo("America/New_York"))
    assert (local.hour, local.minute) == (3, 0)


def test_dst_fall_uses_first_occurrence():
    result = monthly_occurrence(2026, 11, 1, time(1, 30), "America/New_York")
    assert result == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)


def test_next_occurrence_and_cycle_key():
    result = next_occurrence(datetime(2026, 8, 16, 7, tzinfo=timezone.utc), 16, time(2, 23), "America/New_York")
    assert result.month == 9
    assert cycle_key(result, "America/New_York") == "2026-09"

