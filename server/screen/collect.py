"""Turns upstream responses into a Snapshot, with a cache per source.

Each source is fetched only when its cached copy has expired, and a failed
fetch falls back to the last good copy while that is young enough. The
Den Haag HS trips follow the firmware's conditional fetch: refreshed every
45 minutes while Centraal runs clean, fetched fresh as soon as Centraal
shows a disruption.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import sources, trains
from .config import Settings
from .model import Snapshot
from .weather import pick_current

log = logging.getLogger(__name__)

# key: (refresh after, give up on the cached copy after)
POLICY = {
    "om_hourly": (timedelta(minutes=15), timedelta(hours=3)),
    "om_daily": (timedelta(hours=6), timedelta(hours=24)),
    "br_feed": (timedelta(minutes=10), timedelta(hours=3)),
    "br_rain": (timedelta(minutes=5), timedelta(minutes=30)),
    "ns_ctr": (timedelta(minutes=5), timedelta(0)),       # never served from cache
    "ns_hs": (timedelta(minutes=45), timedelta(minutes=45)),
}


@dataclass
class _Entry:
    value: Any
    fetched_at: datetime


@dataclass
class Collector:
    settings: Settings
    fetcher: Any
    cache: dict[str, _Entry] = field(default_factory=dict)

    def _get(self, key: str, now: datetime, *, force: bool = False, fresh_when=None):
        """Parsed value for `key`, or None. `fresh_when(entry)` can declare a
        cached entry unusable (e.g. the hourly list from a previous hour)."""
        refresh, max_age = POLICY[key]
        entry = self.cache.get(key)
        usable = entry is not None and (fresh_when is None or fresh_when(entry))
        if usable and not force and now - entry.fetched_at < refresh:
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
            if usable and now - entry.fetched_at < max_age:
                return entry.value
            return None

    def snapshot(self, now: datetime) -> Snapshot:
        s = self.settings
        snap = Snapshot(now=now)

        # The hourly list starts at the hour it was fetched in.
        same_hour = lambda e: e.fetched_at.replace(minute=0, second=0, microsecond=0) == \
            now.replace(minute=0, second=0, microsecond=0)
        snap.hourly = self._get("om_hourly", now, fresh_when=same_hour) or []

        # "Today" must be today.
        same_day = lambda e: e.fetched_at.date() == now.date()
        snap.forecast = self._get("om_daily", now, fresh_when=same_day) or []

        stations = self._get("br_feed", now)
        if stations:
            snap.weather = pick_current(
                stations, now, s.latitude, s.longitude,
                stale_min=s.buienradar_stale_min,
                consensus_km=s.buienradar_consensus_km,
                max_candidates=s.buienradar_max_candidates)

        snap.rain = self._get("br_rain", now) or []

        raw_ctr = self._get("ns_ctr", now) or []
        ctr = trains.filter_dominated(raw_ctr)
        disrupted = trains.ctr_has_disruption(ctr)
        hs = self._get("ns_hs", now, force=disrupted)
        snap.departures = trains.pick_departures(ctr, trains.filter_dominated(hs or []), now)
        snap.trains_ok = bool(raw_ctr or hs)
        return snap
