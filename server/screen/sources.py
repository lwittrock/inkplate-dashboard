"""The four upstream APIs: fetching raw bodies, and parsing them into the model.

Parsers are pure and tolerant: a missing or mistyped field gets a neutral
default rather than an exception, so one odd value never costs a section.
"""

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .model import Departure, DayForecast, RainSample, Station, Transfer, round_half_away
from .weather import daily_category

TZ = ZoneInfo("Europe/Amsterdam")
TIMEOUT_S = 10
RETRIES = 3
USER_AGENT = "inkplate-screen (+https://github.com/lwittrock/inkplate-dashboard)"

BUIENRADAR_FEED_URL = "https://data.buienradar.nl/2.0/feed/json"
BUIENRADAR_RAIN_URL = "https://gpsgadget.buienradar.nl/data/raintext?lat={lat:.2f}&lon={lon:.2f}"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NS_TRIPS_URL = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips"

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # not %a: that follows the locale

DAILY_FIELDS = (
    "temperature_2m_max,temperature_2m_min,weather_code,"
    "sunshine_duration,daylight_duration,precipitation_sum,precipitation_hours,snowfall_sum,"
    "sunrise,sunset,"
    "wind_speed_10m_max,wind_gusts_10m_max,apparent_temperature_max,uv_index_max"
)


class FetchError(Exception):
    pass


def http_get(url: str, headers: dict | None = None) -> bytes:
    """GET with up to three attempts, a second apart."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read()
        except Exception as exc:  # network errors, HTTP errors (urllib raises on non-2xx), timeouts
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(1)
    raise FetchError(f"{url.split('?')[0]}: {last}")


# --- field readers ------------------------------------------------------------

def num(v, default: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def text(v, default: str = "") -> str:
    return v if isinstance(v, str) else default


def local_time(v) -> datetime | None:
    """ISO 8601 -> naive Amsterdam wall-clock time. Accepts "+0200" style
    offsets (NS) and no offset at all (Buienradar, taken as local)."""
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t.astimezone(TZ).replace(tzinfo=None) if t.tzinfo else t


def hhmm_of(v) -> str:
    """"2026-09-23T07:31" -> "07:31"."""
    s = text(v)
    return s[11:16] if len(s) >= 16 else ""


# --- URLs ------------------------------------------------------------------------

def om_hourly_url(s: Settings) -> str:
    return (f"{OPEN_METEO_URL}?latitude={s.latitude:.4f}&longitude={s.longitude:.4f}"
            "&hourly=temperature_2m&forecast_hours=24&timezone=auto")


def om_daily_url(s: Settings) -> str:
    return (f"{OPEN_METEO_URL}?latitude={s.latitude:.4f}&longitude={s.longitude:.4f}"
            f"&daily={DAILY_FIELDS}&forecast_days=7&timezone=auto")


def br_rain_url(s: Settings) -> str:
    return BUIENRADAR_RAIN_URL.format(lat=s.latitude, lon=s.longitude)


def ns_trips_url(from_station: str, to_station: str, max_count: int = 6) -> str:
    return f"{NS_TRIPS_URL}?fromStation={from_station}&toStation={to_station}&maxJourneys={max_count}"


# --- parsers -----------------------------------------------------------------------

def parse_om_hourly(doc: dict) -> list[float]:
    temps = (doc.get("hourly") or {}).get("temperature_2m")
    return [num(v) for v in temps[:24]] if isinstance(temps, list) else []


def parse_om_daily(doc: dict) -> list[DayForecast]:
    daily = doc.get("daily") or {}

    def at(name: str, i: int):
        col = daily.get(name)
        return col[i] if isinstance(col, list) and i < len(col) else None

    days = daily.get("temperature_2m_max")
    out = []
    for i in range(min(7, len(days) if isinstance(days, list) else 0)):
        cat, sunny = daily_category(
            api_code=int(num(at("weather_code", i))),
            precip_sum=num(at("precipitation_sum", i)),
            precip_hours=num(at("precipitation_hours", i)),
            snowfall_sum=num(at("snowfall_sum", i)),
            sunshine_h=num(at("sunshine_duration", i)) / 3600,
            daylight_h=num(at("daylight_duration", i)) / 3600,
        )
        if i == 0:
            day_name = "Today"
        else:
            try:
                day_name = DAY_ABBR[datetime.strptime(text(at("time", i))[:10], "%Y-%m-%d").weekday()]
            except ValueError:
                day_name = "?"
        out.append(DayForecast(
            day_name=day_name,
            temp_max=round_half_away(num(at("temperature_2m_max", i))),
            temp_min=round_half_away(num(at("temperature_2m_min", i))),
            feels_max=round_half_away(num(at("apparent_temperature_max", i))),
            category=cat,
            sunny_variant=sunny,
            sunrise=hhmm_of(at("sunrise", i)),
            sunset=hhmm_of(at("sunset", i)),
            wind_max_kmh=num(at("wind_speed_10m_max", i)),
            gust_max_kmh=num(at("wind_gusts_10m_max", i)),
            uv_max=num(at("uv_index_max", i)),
        ))
    return out


_DUTCH_CARDINALS = {
    "N": 0, "NNO": 23, "NO": 45, "ONO": 68, "O": 90, "OZO": 113, "ZO": 135, "ZZO": 158,
    "Z": 180, "ZZW": 203, "ZW": 225, "WZW": 248, "W": 270, "WNW": 293, "NW": 315, "NNW": 338,
}


def icon_code_from_url(iconurl) -> str:
    """".../weather/30x30/aa.png" -> "a". Buienradar doubles the letter for the
    day variant; "cc" is the exception and has its own meaning."""
    url = text(iconurl)
    stem = url[url.rfind("/") + 1:url.rfind(".")] if "/" in url and "." in url else ""
    code = stem.lower()
    if len(code) == 2 and code[0] == code[1] and code != "cc":
        code = code[0]
    return code if len(code) <= 2 else ""


def parse_br_stations(doc: dict) -> list[Station]:
    """Stations with a weather icon and a temperature; the others are useless."""
    raw = (doc.get("actual") or {}).get("stationmeasurements")
    out = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        code = icon_code_from_url(s.get("iconurl"))
        if not code or s.get("temperature") is None:
            continue
        bearing = s.get("winddirectiondegrees")
        if not isinstance(bearing, (int, float)) or bearing < 0:
            bearing = _DUTCH_CARDINALS.get(text(s.get("winddirection")), 0)
        out.append(Station(
            name=text(s.get("stationname")),
            lat=num(s.get("lat")),
            lon=num(s.get("lon")),
            observed=local_time(s.get("timestamp")),
            icon_code=code,
            temp=num(s.get("temperature")),
            wind_ms=num(s.get("windspeed")),
            bearing=int(bearing),
            feels=num(s.get("feeltemperature"), None),
            gust_ms=num(s.get("windgusts"), None),
        ))
    return out


def parse_br_rain(body: str) -> list[RainSample]:
    """Lines of "VVV|HH:MM" at 5-minute steps, VVV on Buienradar's log scale:
    mm/h = 10 ^ ((VVV - 109) / 32), and 0 means dry."""
    out = []
    for line in body.splitlines():
        value, sep, label = line.partition("|")
        label = label.strip()
        if not sep or len(label) < 5:
            continue
        try:
            v = int(value.strip())
        except ValueError:
            continue
        out.append(RainSample(0.0 if v <= 0 else 10 ** ((v - 109) / 32), label[:5]))
    return out[:24]


def _minutes_late(leg_end: dict) -> int:
    planned, actual = local_time(leg_end.get("plannedDateTime")), local_time(leg_end.get("actualDateTime"))
    if not planned or not actual:
        return 0
    return int((actual - planned).total_seconds() // 60)


def parse_trips(doc: dict, origin: str, max_count: int = 6) -> list[Departure]:
    trips = doc.get("trips")
    out: list[Departure] = []
    for trip in trips if isinstance(trips, list) else []:
        if len(out) >= max_count:
            break
        legs = trip.get("legs") if isinstance(trip, dict) else None
        if not isinstance(legs, list) or not legs:
            continue
        first, last = legs[0], legs[-1]
        start = first.get("origin") or {}
        planned = local_time(start.get("plannedDateTime"))
        if planned is None:
            continue
        # No actual time means no realtime data yet, not a cancellation.
        departs = local_time(start.get("actualDateTime")) or planned
        cancelled = bool(first.get("cancelled") or first.get("partCancelled"))

        end = last.get("destination") or {}
        arrives = local_time(end.get("actualDateTime")) or local_time(end.get("plannedDateTime"))

        # The transfer is the last leg: the Breda -> Tilburg Universiteit sprinter.
        if len(legs) == 1:
            transfer = Transfer.OK
        elif last.get("cancelled") or last.get("partCancelled"):
            transfer = Transfer.CANCELLED
        elif _minutes_late(last.get("origin") or {}) >= 5:
            transfer = Transfer.LATE
        else:
            transfer = Transfer.OK

        out.append(Departure(
            origin=origin,
            planned=planned,
            departs=departs,
            arrives=arrives,
            track=text(start.get("plannedTrack"), "?"),
            delay_min=0 if cancelled else max(_minutes_late(start), 0),
            cancelled=cancelled,
            transfer=transfer,
            leg_count=len(legs),
        ))
    return out


# --- fetchers ------------------------------------------------------------------------
# A fetcher returns the raw response body for a source key. The collector
# parses; fixtures replay recorded bodies through the same parsers.

KEYS = ("om_hourly", "om_daily", "br_feed", "br_rain", "ns_ctr", "ns_hs")
FIXTURE_FILES = {
    "om_hourly": "om_hourly.json", "om_daily": "om_daily.json",
    "br_feed": "br_feed.json", "br_rain": "br_rain.txt",
    "ns_ctr": "ns_ctr.json", "ns_hs": "ns_hs.json",
}


class LiveFetcher:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def get(self, key: str) -> bytes:
        s = self.s
        if key == "om_hourly":
            return http_get(om_hourly_url(s))
        if key == "om_daily":
            return http_get(om_daily_url(s))
        if key == "br_feed":
            return http_get(BUIENRADAR_FEED_URL)
        if key == "br_rain":
            return http_get(br_rain_url(s))
        if key in ("ns_ctr", "ns_hs"):
            if not s.ns_api_key:
                raise FetchError("NS_API_KEY is not set")
            station = s.station_central if key == "ns_ctr" else s.station_hs
            return http_get(ns_trips_url(station, s.station_destination),
                            {"Ocp-Apim-Subscription-Key": s.ns_api_key})
        raise KeyError(key)


class FixtureFetcher:
    """Replays a recorded set of responses. A missing file is a failed fetch."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def get(self, key: str) -> bytes:
        path = self.folder / FIXTURE_FILES[key]
        if not path.exists():
            raise FetchError(f"no fixture {path.name}")
        return path.read_bytes()


class RecordingFetcher:
    """Fetches through another fetcher and saves each body into a fixture folder."""

    def __init__(self, inner, folder: Path) -> None:
        self.inner, self.folder = inner, folder

    def get(self, key: str) -> bytes:
        body = self.inner.get(key)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / FIXTURE_FILES[key]).write_bytes(body)
        return body


def parse(key: str, body: bytes):
    if key == "br_rain":
        return parse_br_rain(body.decode("utf-8", "replace"))
    doc = json.loads(body)
    if not isinstance(doc, dict):
        return []
    return {
        "om_hourly": parse_om_hourly,
        "om_daily": parse_om_daily,
        "br_feed": parse_br_stations,
        "ns_ctr": lambda d: parse_trips(d, "CTR"),
        "ns_hs": lambda d: parse_trips(d, "HS"),
    }[key](doc)
