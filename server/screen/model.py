"""Data passed from the sources, through the logic, to the renderer.

All times are naive local datetimes (Europe/Amsterdam wall clock).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum
from typing import NamedTuple


class Category(IntEnum):
    CLEAR = 0
    PARTLY_CLOUDY = 1
    OVERCAST = 2
    FOG = 3
    DRIZZLE = 4
    RAIN = 5
    RAIN_HEAVY = 6
    SNOW = 7
    THUNDERSTORM = 8
    SHOWERS = 9                 # sun and showers: a few wet hours on a day that is otherwise bright


RAIN_FAMILY = {Category.DRIZZLE, Category.RAIN, Category.RAIN_HEAVY, Category.THUNDERSTORM,
               Category.SHOWERS}


class Transfer(IntEnum):
    """The connection at Breda onto the sprinter to Tilburg Universiteit."""
    OK = 0
    LATE = 1
    CANCELLED = 2


@dataclass
class Departure:
    origin: str                 # "CTR" (Den Haag Centraal) or "HS" (Den Haag HS)
    planned: datetime           # planned departure; NS repeats a train under several routings with this
    departs: datetime           # actual departure if known, else planned
    arrives: datetime | None    # at Tilburg Universiteit, actual if known
    track: str
    delay_min: int              # 0 when on time, unknown, or cancelled
    cancelled: bool
    transfer: Transfer
    leg_count: int

    @property
    def time(self) -> str:
        return f"{self.departs:%H:%M}"

    @property
    def uni_arr(self) -> str:
        return f"{self.arrives:%H:%M}" if self.arrives else ""


@dataclass
class DayForecast:
    day_name: str               # "Today", "Thu", ...
    temp_max: int               # rounded, as displayed
    temp_min: int
    feels_max: int
    category: Category
    sunrise: str                # "HH:MM", "" if unknown
    sunset: str
    wind_max_kmh: float
    gust_max_kmh: float
    uv_max: float
    day: date | None = None     # the date, for /data; None where a test or mockup builds one by hand


@dataclass
class HourForecast:
    """One hour of Open-Meteo's forecast. The sums (precipitation, snow,
    sunshine) cover the hour before `time`; temperature and cloud are at `time`."""
    time: datetime
    temp: float
    code: int                   # WMO weather code
    precip_mm: float
    snow_cm: float
    sun_s: float
    cloud_pct: float
    cloud_low_pct: float = 0.0          # the layers, logged with NOW's choice
    cloud_mid_pct: float = 0.0
    cloud_high_pct: float = 0.0


@dataclass
class Forecast:
    """Open-Meteo's week: every hour from midnight today, and the days by date."""
    hours: list[HourForecast]
    days: dict[date, DayForecast]


@dataclass
class Station:
    """One Buienradar weather station's latest observation."""
    name: str
    lat: float
    lon: float
    observed: datetime | None
    icon_code: str              # "a", "cc", ... ; "" if the feed gave none
    temp: float
    wind_ms: float
    bearing: int                # degrees the wind comes from
    feels: float | None = None
    gust_ms: float | None = None
    sun_wm2: float | None = None        # measured sunshine ("sunpower"); None without a sensor


@dataclass
class StationReading:
    """One nearby station as NOW's choice saw it."""
    name: str
    km: float
    code: str
    sun_wm2: float | None
    share: float | None         # sun_wm2 as a share of a clear sky's; None without either
    voting: bool                # within the consensus radius


@dataclass
class NowChoice:
    """Why NOW shows what it shows: logged, kept in now.jsonl, shown in /status."""
    shown: Category
    vote: Category
    sun_elevation: float
    clear_wm2: float
    median_share: float | None  # the voters'
    sun_through: bool           # the sunshine rule held, whatever the vote
    hour: HourForecast | None
    stations: list[StationReading]

    def __str__(self) -> str:
        why = "sun through" if self.shown != self.vote else "vote"
        share = "no sunshine data" if self.median_share is None else f"stations {self.median_share:.0%}"
        model = "no model hour" if self.hour is None else f"model {self.hour.sun_s / 60:.0f} min sun"
        return (f"{self.shown.name.lower()} ({why}; vote {self.vote.name.lower()}; {share}, {model}, "
                f"sun {self.sun_elevation:.1f} deg up)")

    def record(self, at: datetime) -> dict:
        h = self.hour
        return {
            "t": at.isoformat(timespec="seconds"),
            "shown": self.shown.name.lower(),
            "vote": self.vote.name.lower(),
            "sun_through": self.sun_through,
            "sun_elevation": round(self.sun_elevation, 1),
            "clear_wm2": round(self.clear_wm2),
            "median_share": None if self.median_share is None else round(self.median_share, 3),
            "model": None if h is None else {
                "sun_min": round(h.sun_s / 60), "cloud": h.cloud_pct, "low": h.cloud_low_pct,
                "mid": h.cloud_mid_pct, "high": h.cloud_high_pct},
            "stations": [{"name": s.name, "km": round(s.km, 1), "code": s.code, "sun_wm2": s.sun_wm2,
                          "share": None if s.share is None else round(s.share, 3), "voting": s.voting}
                         for s in self.stations],
        }


@dataclass
class WeatherNow:
    temp: float
    wind_kmh: float
    category: Category
    wind_bearing: int
    feels: float | None = None
    gust_kmh: float | None = None
    choice: NowChoice | None = None


class RainSample(NamedTuple):
    mmh: float
    label: str                  # Buienradar's own "HH:MM"


@dataclass
class Snapshot:
    """Everything one frame shows. None or empty means "no data for this section"."""
    now: datetime
    weather: WeatherNow | None = None
    rain: list[RainSample] = field(default_factory=list)          # next 2 h, 5-minute steps
    hourly: list[float] = field(default_factory=list)             # next 24 h, from this hour
    hours: list[HourForecast] = field(default_factory=list)       # the same hours in full, for /data
    forecast: list[DayForecast] = field(default_factory=list)
    departures: list[Departure] = field(default_factory=list)
    trains_ok: bool = True      # False: NS gave nothing at all, as opposed to "no trains soon"
    battery_v: float | None = None
    firmware: str | None = None                                   # drawn in the footer when "dev"


def round_half_away(x: float) -> int:
    """Round as people read temperatures: 18.5 -> 19, -0.5 -> -1."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)
