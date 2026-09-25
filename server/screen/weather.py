"""Weather rules: daily categories, Buienradar icon codes, the station vote.

CLAUDE.md ("Buienradar mapping & fallback", "Buienradar consensus picker")
explains why the vote exists and its known caveats. docs/weather-categories.md
explains the daily rules and the data behind their thresholds.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from .model import Category, HourForecast, NowChoice, Station, StationReading, WeatherNow

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Amsterdam")

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


# NOW's sky. Buienradar's icon follows total cloud cover and ignores the sun,
# so a veil of high cloud reads "zwaar bewolkt" while the sun shines through
# it (docs/weather-categories.md, "NOW"). An overcast vote shows partly cloudy
# when the model and the stations both say the sun is getting through.
# From one morning's data; to be checked against a week of logs.
MIN_SUN_ELEVATION = 10.0    # degrees: lower, measured sunshine says little
MODEL_SUN_S = 45 * 60       # the model's sunshine in this hour
SUN_THROUGH = 0.40          # the voters' median sunshine, as a share of a clear sky's


def sun_elevation(lat: float, lon: float, when: datetime) -> float:
    """Degrees above the horizon at a naive local time: NOAA's approximation,
    within about half a degree (plenty for MIN_SUN_ELEVATION)."""
    t = when.replace(tzinfo=TZ).astimezone(timezone.utc)
    g = 2 * math.pi / 365 * (t.timetuple().tm_yday - 1 + (t.hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    hour_angle = math.radians((t.hour * 60 + t.minute + t.second / 60 + eqtime + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl)
                                  + math.cos(la) * math.cos(decl) * math.cos(hour_angle)))


def clear_sky_wm2(elevation: float) -> float:
    """Sunshine on a clear day at this sun height, W/m2 (Haurwitz's model)."""
    s = math.sin(math.radians(elevation))
    return 1098 * s * math.exp(-0.057 / s) if s > 0 else 0.0


def pick_current(stations: list[Station], now: datetime, lat: float, lon: float, *,
                 stale_min: int, consensus_km: float, max_candidates: int,
                 hour: HourForecast | None = None) -> WeatherNow | None:
    """Current conditions from the nearest Buienradar stations.

    Take the nearest `max_candidates` fresh stations, vote on the weather
    category among those within `consensus_km` (a tie goes to the closest),
    and read temperature and wind from the closest station that voted for
    the winner. One station with a faulty sensor can't set the icon alone.
    An overcast vote becomes partly cloudy when the sun gets through (see
    above); `hour` is the forecast hour now falls in. The numbers behind the
    choice come back as WeatherNow.choice, and are logged.
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

    elevation = sun_elevation(lat, lon, now)
    clear = clear_sky_wm2(elevation)

    def share(s: Station) -> float | None:
        return s.sun_wm2 / clear if s.sun_wm2 is not None and clear > 0 else None

    voters = [s for _, s in in_range] or [src]
    shares = [share(s) for s in voters if share(s) is not None]
    mid = median(shares) if shares else None
    sun_through = (elevation >= MIN_SUN_ELEVATION and hour is not None and hour.sun_s >= MODEL_SUN_S
                   and mid is not None and mid >= SUN_THROUGH)
    shown = Category.PARTLY_CLOUDY if cat == Category.OVERCAST and sun_through else cat
    choice = NowChoice(
        shown=shown, vote=cat, sun_elevation=elevation, clear_wm2=clear, median_share=mid,
        sun_through=sun_through, hour=hour,
        stations=[StationReading(name=s.name.removeprefix("Meetstation "), km=d, code=s.icon_code,
                                 sun_wm2=s.sun_wm2, share=share(s), voting=any(s is v for v in voters))
                  for d, s in by_distance[:max_candidates]])
    log.info("now: %s", choice)

    return WeatherNow(temp=src.temp, wind_kmh=src.wind_ms * 3.6, category=shown, wind_bearing=src.bearing,
                      feels=src.feels, gust_kmh=src.gust_ms * 3.6 if src.gust_ms is not None else None,
                      choice=choice)
