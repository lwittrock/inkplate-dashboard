"""Weather rules: daily categories, Buienradar icon codes, the station vote.

Ported from A_Calculations.ino and fetchBuienradarNow in B_Network.ino.
CLAUDE.md ("Buienradar mapping & fallback", "Buienradar consensus picker")
explains why the vote exists and its known caveats.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from .model import Category, WeatherNow


def daily_category(api_code: int, precip_sum: float, precip_hours: int,
                   snowfall_sum: float, sunshine_h: float, daylight_h: float) -> tuple[Category, bool]:
    """(category, use the sun-with-rain/snow icon variant)."""
    # Fog and thunderstorms can't be read from daily aggregates: trust the API code.
    if api_code in (45, 48):
        return Category.FOG, False
    if api_code >= 95:
        return Category.THUNDERSTORM, False

    if snowfall_sum >= 1.0:
        return Category.SNOW, daylight_h > 0 and (sunshine_h / daylight_h) >= 0.5

    ratio = sunshine_h / daylight_h if daylight_h > 0 else 0.0

    # A sunny day with brief showers.
    if ratio >= 0.5 and 0 < precip_sum < 5.0:
        return Category.PARTLY_CLOUDY, False

    if precip_sum >= 10.0:
        return Category.RAIN_HEAVY, ratio >= 0.5
    if precip_sum >= 5.0:
        return Category.RAIN, ratio >= 0.5
    if precip_sum >= 1.0 or precip_hours >= 3:
        return Category.DRIZZLE, ratio >= 0.5

    if ratio >= 0.65:
        return Category.CLEAR, False
    if ratio >= 0.35:
        return Category.PARTLY_CLOUDY, False
    return Category.OVERCAST, False


def icon_code_from_url(iconurl: str | None) -> str:
    """".../weather/30x30/aa.png" -> "a". Doubled letters are the day variant
    and collapse to the single letter; "cc" stays, it has its own meaning."""
    if not iconurl:
        return ""
    slash, dot = iconurl.rfind("/"), iconurl.rfind(".")
    if slash < 0 or dot < 0 or dot <= slash + 1:
        return ""
    code = iconurl[slash + 1:dot]
    if len(code) >= 4:          # the firmware's 4-byte buffer
        return ""
    code = code.lower()
    if len(code) == 2 and code[0] == code[1] and code[0] != "c":
        code = code[0]
    return code


def category_from_icon(code: str) -> Category:
    if not code:
        return Category.OVERCAST
    if code == "cc":
        return Category.OVERCAST
    return {
        "a": Category.CLEAR, "j": Category.CLEAR,
        "b": Category.PARTLY_CLOUDY, "o": Category.PARTLY_CLOUDY, "r": Category.PARTLY_CLOUDY,
        "p": Category.OVERCAST, "c": Category.OVERCAST,
        "d": Category.FOG, "n": Category.FOG,
        "f": Category.DRIZZLE, "k": Category.DRIZZLE,
        "h": Category.RAIN,
        "m": Category.RAIN_HEAVY, "q": Category.RAIN_HEAVY,
        "g": Category.THUNDERSTORM, "s": Category.THUNDERSTORM, "l": Category.THUNDERSTORM,
        "i": Category.SNOW, "u": Category.SNOW, "v": Category.SNOW, "w": Category.SNOW,
    }.get(code[0], Category.OVERCAST)


_DUTCH_CARDINALS = {
    "N": 0, "NNO": 23, "NO": 45, "ONO": 68, "O": 90, "OZO": 113, "ZO": 135, "ZZO": 158,
    "Z": 180, "ZZW": 203, "ZW": 225, "WZW": 248, "W": 270, "WNW": 293, "NW": 315, "NNW": 338,
}


def bearing_from_dutch_cardinal(s: str) -> int:
    return _DUTCH_CARDINALS.get(s or "", -1)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_local(iso: str | None) -> datetime | None:
    """Wall-clock fields of an ISO timestamp, offset ignored (both the APIs and
    the dashboard run on Amsterdam time; see parseISOToLocal)."""
    if not iso or len(iso) < 19:
        return None
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


@dataclass
class _Candidate:
    dist: float
    cat: Category
    temp: float
    ms: float
    bearing: int
    icon: str
    name: str


def _num(v, default: float) -> float:
    """ArduinoJson `v | 0.0f`: any number, else the default."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _int(v, default: int) -> int:
    """ArduinoJson `v | 0`: integers only; a JSON float such as 3.0 gives the default."""
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def pick_current(stations: list[dict], now: datetime, lat: float, lon: float, *,
                 stale_min: int, consensus_km: float, max_candidates: int) -> WeatherNow | None:
    """Current conditions from the Buienradar feed's station list.

    Take the nearest `max_candidates` fresh stations, vote on the weather
    category among those within `consensus_km` (tie: the closest), and read
    temperature and wind from the closest station that voted for the winner.
    """
    cands: list[_Candidate] = []
    for s in stations:
        code = icon_code_from_url(s.get("iconurl") if isinstance(s.get("iconurl"), str) else None)
        if not code:
            continue
        if s.get("temperature") is None:
            continue
        ts = parse_local(s.get("timestamp") if isinstance(s.get("timestamp"), str) else None)
        if ts is not None and (now - ts).total_seconds() > stale_min * 60:
            continue
        bearing = _int(s.get("winddirectiondegrees"), -1)
        if bearing < 0:
            bearing = bearing_from_dutch_cardinal(s.get("winddirection") or "")
            if bearing < 0:
                bearing = 0
        cands.append(_Candidate(
            dist=haversine_km(lat, lon, _num(s.get("lat"), 0.0), _num(s.get("lon"), 0.0)),
            cat=category_from_icon(code),
            temp=_num(s.get("temperature"), 0.0),
            ms=_num(s.get("windspeed"), 0.0),
            bearing=bearing,
            icon=code,
            name=str(s.get("stationname") or ""),
        ))
    if not cands:
        return None

    # Stable sort keeps feed order among equal distances, as the firmware's
    # insertion sort did.
    cands = sorted(cands, key=lambda c: c.dist)[:max_candidates]

    in_range = [c for c in cands if c.dist <= consensus_km]
    winner = cands[0].cat
    if in_range:
        votes: dict[Category, int] = {}
        for c in in_range:
            votes[c.cat] = votes.get(c.cat, 0) + 1
        top = max(votes.values())
        winner = next(c.cat for c in in_range if votes[c.cat] == top)

    src = next((c for c in in_range if c.cat == winner), cands[0])
    return WeatherNow(temp=src.temp, wind_kmh=src.ms * 3.6, category=src.cat, wind_bearing=src.bearing)
