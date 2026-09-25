"""Turns upstream responses into a Snapshot, with a cache per source.

Each source is fetched only when its cached copy has expired, and a failed
fetch falls back to the last good copy while that is young enough. The
Den Haag HS trips follow the firmware's conditional fetch: refreshed every
45 minutes while Centraal runs clean, fetched fresh as soon as Centraal
shows a disruption.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from . import sources, trains
from .config import Settings
from .model import DayForecast, Forecast, HourForecast, Snapshot
from .weather import pick_current

log = logging.getLogger(__name__)

# key: (refresh after, give up on the cached copy after)
POLICY = {
    "om": (timedelta(hours=1), timedelta(hours=12)),        # KNMI's model runs hourly
    "br_feed": (timedelta(minutes=10), timedelta(hours=3)),
    "br_rain": (timedelta(minutes=5), timedelta(minutes=30)),
    "ns_ctr": (timedelta(minutes=5), timedelta(0)),       # never served from cache
    "ns_hs": (timedelta(minutes=45), timedelta(minutes=45)),
}


def hours_from(om: Forecast, now: datetime) -> list[HourForecast]:
    """24 hours from the top of this hour. Empty when the list lacks this
    hour: render.temp_chart takes the first value as it."""
    top = now.replace(minute=0, second=0, microsecond=0)
    start = next((i for i, h in enumerate(om.hours) if h.time >= top), None)
    if start is None or om.hours[start].time != top:
        return []
    return om.hours[start:start + 24]


def hours_ahead(om: Forecast, now: datetime) -> list[float]:
    """The temperatures of hours_from, for the chart."""
    return [h.temp for h in hours_from(om, now)]


def hour_now(om: Forecast, now: datetime) -> HourForecast | None:
    """The forecast hour `now` falls in: its sums are stamped at the hour's end."""
    end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return next((h for h in om.hours if h.time == end), None)


def week_ahead(om: Forecast, now: datetime) -> list[DayForecast]:
    """Up to seven days from today. Empty when today is missing, so the
    first column is always today."""
    today = now.date()
    if today not in om.days:
        return []
    days = [d for day, d in om.days.items() if day >= today][:7]
    return [replace(days[0], day_name="Today")] + days[1:]


@dataclass
class _Entry:
    value: Any
    fetched_at: datetime


@dataclass
class Collector:
    settings: Settings
    fetcher: Any
    cache: dict[str, _Entry] = field(default_factory=dict)

    def _get(self, key: str, now: datetime, *, force: bool = False):
        """Parsed value for `key`, or None."""
        refresh, max_age = POLICY[key]
        entry = self.cache.get(key)
        if entry is not None and not force and now - entry.fetched_at < refresh:
            return entry.value
        try:
            value = sources.parse(key, self.fetcher.get(key))
            if not value:
                # As in the firmware: nothing parsed counts as a failure.
                raise sources.FetchError("empty response")
            self.cache[key] = _Entry(value, now)
            return value
        except Exception as exc:
            log.warning("%s: fetch failed: %s", key, exc)
            if entry is not None and now - entry.fetched_at < max_age:
                return entry.value
            return None

    def snapshot(self, now: datetime) -> Snapshot:
        s = self.settings
        snap = Snapshot(now=now)

        om = self._get("om", now)
        if om:
            snap.hours, snap.forecast = hours_from(om, now), week_ahead(om, now)
            snap.hourly = [h.temp for h in snap.hours]

        stations = self._get("br_feed", now)
        if stations:
            snap.weather = pick_current(
                stations, now, s.latitude, s.longitude,
                stale_min=s.buienradar_stale_min,
                consensus_km=s.buienradar_consensus_km,
                max_candidates=s.buienradar_max_candidates,
                hour=hour_now(om, now) if om else None)

        snap.rain = self._get("br_rain", now) or []

        raw_ctr = self._get("ns_ctr", now) or []
        ctr = trains.filter_dominated(raw_ctr)
        disrupted = trains.ctr_has_disruption(ctr)
        hs = self._get("ns_hs", now, force=disrupted)
        snap.departures = trains.pick_departures(ctr, trains.filter_dominated(hs or []), now)
        snap.trains_ok = bool(raw_ctr or hs)
        return snap
