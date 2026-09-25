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
class WeatherNow:
    temp: float
    wind_kmh: float
    category: Category
    wind_bearing: int
    feels: float | None = None
    gust_kmh: float | None = None


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
    forecast: list[DayForecast] = field(default_factory=list)
    departures: list[Departure] = field(default_factory=list)
    trains_ok: bool = True      # False: NS gave nothing at all, as opposed to "no trains soon"
    battery_v: float | None = None
    firmware: str | None = None                                   # drawn in the footer when "dev"


def round_half_away(x: float) -> int:
    """Round as people read temperatures: 18.5 -> 19, -0.5 -> -1."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)
