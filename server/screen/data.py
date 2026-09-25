"""The last render's data as JSON, served at /data for Home Assistant.

Home Assistant shows the same weather and trains as the wall from this, so
it never fetches them itself and the two cannot disagree. The screen is the
design; this is only what it was drawn from.

- Times carry their UTC offset: HA reads a time without one as UTC.
- Categories are the Category names in lower case ("partly_cloudy"). HA maps
  them to its own conditions, so rename none; adding one is fine.
- A section with no data is null or an empty list, as the renderer treats it.
- An hour's temperature and cloud are at `at`; its precipitation, snow and
  sun minutes cover the hour before (Open-Meteo's convention, see HourForecast).
- Rain times are Buienradar's own "HH:MM" labels, for showing, not computing.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from .model import Departure, Snapshot

TZ = ZoneInfo("Europe/Amsterdam")


def stamp(t: datetime | None) -> str | None:
    """A naive wall-clock time with its Amsterdam offset."""
    return None if t is None else t.replace(tzinfo=TZ).isoformat(timespec="seconds")


def _one(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def _departure(d: Departure) -> dict:
    return {
        "origin": d.origin,
        "planned": stamp(d.planned),
        "departs": stamp(d.departs),
        "arrives": stamp(d.arrives),
        "track": d.track,
        "delay_min": d.delay_min,
        "cancelled": d.cancelled,
        "transfer": d.transfer.name.lower(),
    }


def snapshot_data(snap: Snapshot) -> dict:
    w = snap.weather
    return {
        "rendered_at": stamp(snap.now),
        "weather": {
            "now": None if w is None else {
                "temp": _one(w.temp), "feels": _one(w.feels),
                "wind_kmh": _one(w.wind_kmh), "gust_kmh": _one(w.gust_kmh),
                "wind_bearing": w.wind_bearing, "category": w.category.name.lower(),
            },
            "rain": [{"time": r.label, "mmh": round(r.mmh, 2)} for r in snap.rain],
            "hours": [{"at": stamp(h.time), "temp": _one(h.temp), "precip_mm": round(h.precip_mm, 1),
                       "snow_cm": round(h.snow_cm, 1), "sun_min": round(h.sun_s / 60),
                       "cloud_pct": round(h.cloud_pct)} for h in snap.hours],
            "days": [{"date": d.day.isoformat() if d.day else None, "name": d.day_name,
                      "temp_max": d.temp_max, "temp_min": d.temp_min, "feels_max": d.feels_max,
                      "category": d.category.name.lower(), "sunrise": d.sunrise, "sunset": d.sunset,
                      "wind_max_kmh": _one(d.wind_max_kmh), "gust_max_kmh": _one(d.gust_max_kmh),
                      "uv_max": _one(d.uv_max)} for d in snap.forecast],
        },
        "trains": {
            "ok": snap.trains_ok,
            "departures": [_departure(d) for d in snap.departures],
        },
    }
