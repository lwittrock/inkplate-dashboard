"""Weather rules: daily categories, Buienradar icon codes, the station vote.

CLAUDE.md ("Buienradar mapping & fallback", "Buienradar consensus picker")
explains why the vote exists and its known caveats. docs/weather-categories.md
explains the daily rules and the data behind their thresholds.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import Category, HourForecast, Station, WeatherNow

# A day is judged on 07:00-21:00. Hourly sums cover the hour before their
# timestamp, so that is the hours stamped 08:00 to 21:00.
FIRST_HOUR, LAST_HOUR = 8, 21

WET_MM = 0.3            # an hour with less is a trace, not rain
SNOW_CM = 0.1           # 0.07 cm traces made a sunny 6 January snowy


@dataclass(frozen=True)
class DayCounts:
    """What a day's window holds: hours per kind, and daylight hours per sky."""
    wet: int = 0
    rain_mm: float = 0.0        # the window's total
    snow: int = 0
    fog: int = 0
    thunder: int = 0
    clear: int = 0
    partly: int = 0
    overcast: int = 0

    def __str__(self) -> str:
        return (f"wet {self.wet} h, {self.rain_mm:.1f} mm, snow {self.snow} h, fog {self.fog} h, "
                f"thunder {self.thunder} h, sky {self.clear} clear/{self.partly} partly/"
                f"{self.overcast} overcast")


def hour_sky(sun_s: float, cloud_pct: float) -> str:
    """Sunshine and cloud together: KNMI's model often gives 95-100% cloud and
    a full hour of sun in the same hour (thin high cloud), which is neither."""
    if sun_s >= 45 * 60 and cloud_pct < 50:
        return "clear"
    if sun_s < 15 * 60 and cloud_pct >= 80:
        return "overcast"
    return "partly"


def count_day(hours: list[HourForecast], sunrise: datetime, sunset: datetime) -> DayCounts:
    """Counts over one day's window. `hours` may hold other days; the window
    is taken from sunrise's date. The sky counts only daylight hours: at least
    half of the hour between sunrise and sunset."""
    day = sunrise.date()
    window = [h for h in hours if h.time.date() == day and FIRST_HOUR <= h.time.hour <= LAST_HOUR]
    sky = {"clear": 0, "partly": 0, "overcast": 0}
    for h in window:
        lit = min(h.time, sunset) - max(h.time - timedelta(hours=1), sunrise)
        if lit >= timedelta(minutes=30):
            sky[hour_sky(h.sun_s, h.cloud_pct)] += 1
    return DayCounts(
        wet=sum(h.precip_mm >= WET_MM for h in window),
        rain_mm=sum(h.precip_mm for h in window),
        snow=sum(h.snow_cm >= SNOW_CM for h in window),
        fog=sum(h.code in (45, 48) for h in window),
        thunder=sum(h.code >= 95 for h in window),
        **sky,
    )


def _rain(mm: float) -> Category:
    if mm >= 10.0:
        return Category.RAIN_HEAVY
    if mm >= 3.0:
        return Category.RAIN
    return Category.DRIZZLE


def day_category(c: DayCounts) -> Category:
    """What most of the day is like. First match wins."""
    daylight = c.clear + c.partly + c.overcast
    if c.clear > daylight / 2:
        sky = Category.CLEAR
    elif c.overcast > daylight / 2:
        sky = Category.OVERCAST
    else:
        sky = Category.PARTLY_CLOUDY

    if c.snow >= 2:
        return Category.SNOW
    if c.thunder >= 2:
        return Category.THUNDERSTORM
    if c.wet >= 5:
        return _rain(c.rain_mm)
    if c.fog >= 3:
        return Category.FOG
    if c.wet >= 2:
        return _rain(c.rain_mm) if sky == Category.OVERCAST else Category.SHOWERS
    return sky


# Buienradar's icon letters, as python-buienradar and Home Assistant read them.
_ICON_CATEGORIES = {
    "a": Category.CLEAR,
    "b": Category.PARTLY_CLOUDY, "j": Category.PARTLY_CLOUDY, "o": Category.PARTLY_CLOUDY,
    "r": Category.PARTLY_CLOUDY,
    "c": Category.OVERCAST, "p": Category.OVERCAST,
    "d": Category.FOG, "n": Category.FOG,
    # "Afwisselend bewolkt met (lichte) regen": drawn as plain rain at night.
    "f": Category.SHOWERS, "h": Category.SHOWERS, "k": Category.SHOWERS,
    "m": Category.DRIZZLE,
    "l": Category.RAIN, "q": Category.RAIN,
    "g": Category.THUNDERSTORM, "s": Category.THUNDERSTORM,
    "i": Category.SNOW, "t": Category.SNOW, "u": Category.SNOW, "v": Category.SNOW,
    "w": Category.SNOW,
}


def known_icon(code: str) -> bool:
    return code == "cc" or code[:1] in _ICON_CATEGORIES


def category_from_icon(code: str) -> Category:
    """Buienradar icon code ("a", "cc", ...) to category; unknown codes are overcast."""
    if code == "cc":
        return Category.OVERCAST
    return _ICON_CATEGORIES.get(code[:1], Category.OVERCAST)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def pick_current(stations: list[Station], now: datetime, lat: float, lon: float, *,
                 stale_min: int, consensus_km: float, max_candidates: int) -> WeatherNow | None:
    """Current conditions from the nearest Buienradar stations.

    Take the nearest `max_candidates` fresh stations, vote on the weather
    category among those within `consensus_km` (a tie goes to the closest),
    and read temperature and wind from the closest station that voted for
    the winner. One station with a faulty sensor can't set the icon alone.
    """
    fresh = [s for s in stations
             if s.observed is None or (now - s.observed).total_seconds() <= stale_min * 60]
    if not fresh:
        return None
    by_distance = sorted(((haversine_km(lat, lon, s.lat, s.lon), s) for s in fresh), key=lambda p: p[0])
    candidates = [(category_from_icon(s.icon_code), s) for d, s in by_distance[:max_candidates]]
    in_range = [(cat, s) for (cat, s), (d, _) in zip(candidates, by_distance) if d <= consensus_km]

    if in_range:
        votes: dict[Category, int] = {}
        for cat, _ in in_range:
            votes[cat] = votes.get(cat, 0) + 1
        top = max(votes.values())
        # in_range is nearest first, so the first category with the top count
        # wins the tie, and that same entry is the closest station voting for it.
        cat, src = next((cat, s) for cat, s in in_range if votes[cat] == top)
    else:
        cat, src = candidates[0]

    return WeatherNow(temp=src.temp, wind_kmh=src.wind_ms * 3.6, category=cat, wind_bearing=src.bearing,
                      feels=src.feels, gust_kmh=src.gust_ms * 3.6 if src.gust_ms is not None else None)
