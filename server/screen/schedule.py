"""When the device wakes next, and what it does on this wake.

The cadence the firmware used (nextSleepSeconds, handleNightMode), moved to
the server: weekday commute windows every 15 minutes, the rest of a weekday
every 30, weekends every 15 all day, and no updates from 23:30 to 06:30.
The night has one wake at 00:05 for the OTA check.

Each wake is aimed 30 seconds past a render slot (every 5 minutes on the
clock), so the frame the device gets is at most half a minute old. The
ESP32's sleep timer drifts a few percent; recomputing on every wake keeps
that from adding up.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")

NIGHT_START = time(23, 30)
NIGHT_END = time(6, 30)
OTA_WAKE = time(0, 5)
PEAK_WINDOWS = ((time(6, 30), time(9, 30)), (time(16, 0), time(19, 30)))  # weekdays
PEAK = timedelta(minutes=15)
OFF_PEAK = timedelta(minutes=30)
RENDER_SLOT = timedelta(minutes=5)
LAND_AFTER_SLOT = timedelta(seconds=30)
MIN_SLEEP = timedelta(minutes=5)   # the device clamps X-Sleep to at least 300 s
FULL_REFRESH_EVERY = 4    # partial refreshes in between leave ghosting; clear it once an hour at peak


@dataclass(frozen=True)
class Plan:
    sleep_s: int
    draw: bool          # False: 204, keep the panel as it is
    ota: bool           # ask the device to check the OTA manifest now


def in_night(t: time) -> bool:
    return t >= NIGHT_START or t < NIGHT_END


def night_key(now: datetime) -> date:
    """The morning a night belongs to: 23:40 on the 23rd and 03:00 on the 24th are both the 24th."""
    return now.date() if now.time() < NIGHT_END else now.date() + timedelta(days=1)


def cadence(now: datetime) -> timedelta:
    if now.weekday() >= 5:
        return PEAK
    t = now.time()
    return PEAK if any(start <= t < end for start, end in PEAK_WINDOWS) else OFF_PEAK


def slot_at_or_after(t: datetime) -> datetime:
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = -(-(t - midnight) // RENDER_SLOT)       # ceiling division
    return midnight + slots * RENDER_SLOT


def seconds_until(now: datetime, target: datetime) -> int:
    """Real seconds between two local wall-clock times, correct across a DST
    switch (subtracting the naive times would be an hour off that night)."""
    return round(target.replace(tzinfo=TZ).timestamp() - now.replace(tzinfo=TZ).timestamp())


def _not_too_soon(now: datetime, target: datetime) -> datetime:
    """A wake at 06:26 is still night, but 06:30:30 is under the device's
    minimum sleep: take the first slot at least five minutes out instead."""
    return max(target, slot_at_or_after(now + MIN_SLEEP) + LAND_AFTER_SLOT)


def plan(now: datetime, *, failing: bool, ota_sent_for: date | None) -> Plan:
    """`failing`: the device reports failed wakes before this one, so its
    panel may say "Server down" and must be redrawn even at night.
    `ota_sent_for`: the night (see night_key) the last OTA hint went out."""
    if in_night(now.time()):
        key = night_key(now)
        after_midnight = now.time() < NIGHT_END
        if after_midnight:
            target = datetime.combine(key, NIGHT_END) + LAND_AFTER_SLOT
        else:
            target = datetime.combine(key, OTA_WAKE) + LAND_AFTER_SLOT
        return Plan(seconds_until(now, _not_too_soon(now, target)), draw=failing,
                    ota=after_midnight and ota_sent_for != key)

    target = slot_at_or_after(now + cadence(now) - RENDER_SLOT / 2) + LAND_AFTER_SLOT
    if in_night(target.time()):
        target = datetime.combine(night_key(target), OTA_WAKE) + LAND_AFTER_SLOT
    return Plan(seconds_until(now, _not_too_soon(now, target)), draw=True, ota=False)


def refresh_mode(*, wake: int | None, failing: bool, new_firmware: bool) -> str:
    """"full" clears ghosting (every 4th wake, as FULL_REFRESH_EVERY did),
    replaces a "Server down" screen, and greets new firmware; else "partial"."""
    if failing or new_firmware or wake is None or wake % FULL_REFRESH_EVERY == 0:
        return "full"
    return "partial"
