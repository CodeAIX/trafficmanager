import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {name}") from exc


def _valid_local(naive: datetime, zone: ZoneInfo, fold: int = 0) -> bool:
    aware = naive.replace(tzinfo=zone, fold=fold)
    return aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive


def localize(naive: datetime, zone: ZoneInfo) -> datetime:
    # fold=0 selects the first occurrence during fall-back.
    if _valid_local(naive, zone, 0):
        return naive.replace(tzinfo=zone, fold=0)
    # Spring-forward gap: advance to the first valid minute.
    candidate = naive
    for _ in range(180):
        candidate += timedelta(minutes=1)
        if _valid_local(candidate, zone, 0):
            return candidate.replace(tzinfo=zone, fold=0)
    raise ValueError("Could not resolve local time")


def monthly_occurrence(year: int, month: int, day: int, at: time, timezone_name: str, missing_day_policy: str = "LAST_DAY") -> datetime | None:
    if day < 1 or day > 31:
        raise ValueError("monthly_day must be between 1 and 31")
    last = calendar.monthrange(year, month)[1]
    if day > last and missing_day_policy == "SKIP":
        return None
    actual_day = min(day, last)
    return localize(datetime.combine(date(year, month, actual_day), at), validate_timezone(timezone_name)).astimezone(timezone.utc)


def next_occurrence(after: datetime, day: int, at: time, timezone_name: str, missing_day_policy: str = "LAST_DAY") -> datetime:
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    local = after.astimezone(validate_timezone(timezone_name))
    year, month = local.year, local.month
    for _ in range(36):
        occurrence = monthly_occurrence(year, month, day, at, timezone_name, missing_day_policy)
        if occurrence is not None and occurrence > after.astimezone(timezone.utc):
            return occurrence
        month += 1
        if month == 13:
            year, month = year + 1, 1
    raise RuntimeError("Unable to calculate next monthly occurrence")


def cycle_key(when_utc: datetime, timezone_name: str) -> str:
    return when_utc.astimezone(validate_timezone(timezone_name)).strftime("%Y-%m")

