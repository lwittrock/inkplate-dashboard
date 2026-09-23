"""Weather rules: daily categories, Buienradar icon codes, the station vote.

CLAUDE.md ("Buienradar mapping & fallback", "Buienradar consensus picker")
explains why the vote exists and its known caveats.
"""

import math
from datetime import datetime

from .model import Category, Station, WeatherNow


def daily_category(api_code: int, precip_sum: float, precip_hours: float,
                   snowfall_sum: float, sunshine_h: float, daylight_h: float) -> tuple[Category, bool]:
    """(category, use the sun-with-rain/snow icon variant) for one forecast day."""
    # Fog and thunderstorms can't be read from daily aggregates: trust the API code.
    if api_code in (45, 48):
        return Category.FOG, False
    if api_code >= 95:
        return Category.THUNDERSTORM, False

    sunny = daylight_h > 0 and sunshine_h / daylight_h >= 0.5
    if snowfall_sum >= 1.0:
        return Category.SNOW, sunny

    # A sunny day with brief showers.
    if sunny and 0 < precip_sum < 5.0:
        return Category.PARTLY_CLOUDY, False

    if precip_sum >= 10.0:
        return Category.RAIN_HEAVY, sunny
    if precip_sum >= 5.0:
        return Category.RAIN, sunny
    if precip_sum >= 1.0 or precip_hours >= 3:
        return Category.DRIZZLE, sunny

    ratio = sunshine_h / daylight_h if daylight_h > 0 else 0.0
    if ratio >= 0.65:
        return Category.CLEAR, False
    if ratio >= 0.35:
        return Category.PARTLY_CLOUDY, False
    return Category.OVERCAST, False


_ICON_CATEGORIES = {
    "a": Category.CLEAR, "j": Category.CLEAR,
    "b": Category.PARTLY_CLOUDY, "o": Category.PARTLY_CLOUDY, "r": Category.PARTLY_CLOUDY,
    "p": Category.OVERCAST, "c": Category.OVERCAST,
    "d": Category.FOG, "n": Category.FOG,
    "f": Category.DRIZZLE, "k": Category.DRIZZLE,
    "h": Category.RAIN,
    "m": Category.RAIN_HEAVY, "q": Category.RAIN_HEAVY,
    "g": Category.THUNDERSTORM, "s": Category.THUNDERSTORM, "l": Category.THUNDERSTORM,
    "i": Category.SNOW, "u": Category.SNOW, "v": Category.SNOW, "w": Category.SNOW,
}


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

    return WeatherNow(temp=src.temp, wind_kmh=src.wind_ms * 3.6, category=cat, wind_bearing=src.bearing)
