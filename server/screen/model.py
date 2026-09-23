"""Data passed from the sources, through the logic, to the renderer."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


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


RAIN_FAMILY = {Category.DRIZZLE, Category.RAIN, Category.RAIN_HEAVY, Category.THUNDERSTORM}


class Transfer(IntEnum):
    OK = 0
    LATE = 1
    CANCELLED = 2


@dataclass
class Departure:
    origin: str               # "CTR" (Den Haag Centraal) or "HS" (Den Haag HS)
    time: str                 # "HH:MM", actual departure, else planned
    track: str
    delay_min: int            # 0 = no delay shown; set only when >= 1 and not cancelled
    cancelled: bool
    uni_arr: str              # "HH:MM" at Tilburg Universiteit, "" if unknown
    transfer: Transfer
    planned_iso: str          # "2026-05-24T12:19:00+0200"
    leg_count: int


@dataclass
class DayForecast:
    day_name: str             # "Today", "Mon", ...
    temp_max: int
    temp_min: int
    rain_prob: int
    category: Category
    sunny_variant: bool
    sunrise: str              # "HH:MM"
    sunset: str
    wind_max_kmh: int
    gust_max_kmh: int
    feels_max: int
    uv_max_x10: int


@dataclass
class WeatherNow:
    temp: float
    wind_kmh: float
    category: Category
    wind_bearing: int         # degrees the wind comes from


@dataclass
class Snapshot:
    """Everything one frame shows. None / empty means "no data for this section"."""
    now: datetime             # local (Europe/Amsterdam), naive
    weather: WeatherNow | None = None
    rain: list[tuple[float, str]] = field(default_factory=list)   # (mm/h, "HH:MM")
    hourly: list[float] = field(default_factory=list)             # next 24 h, from this hour
    forecast: list[DayForecast] = field(default_factory=list)
    departures: list[Departure] = field(default_factory=list)
    battery_v: float | None = None
    firmware: str | None = None   # drawn in the footer only when set
