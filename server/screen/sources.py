"""The four upstream APIs: fetching (network) and parsing (pure).

Ported from B_Network.ino. The parsers follow the firmware's reading of each
field, including ArduinoJson's defaults for missing or mistyped values, so a
render from the same response matches the wall.
"""

import json
import time
import urllib.request
from datetime import datetime

from .config import Settings
from .model import Departure, DayForecast, Transfer
from .trains import calculate_delay
from .weather import _int, _num, daily_category

TIMEOUT_S = 10
RETRIES = 3
USER_AGENT = "inkplate-screen (+https://github.com/lwittrock/inkplate-dashboard)"

BUIENRADAR_FEED_URL = "https://data.buienradar.nl/2.0/feed/json"
BUIENRADAR_RAIN_URL = "https://gpsgadget.buienradar.nl/data/raintext?lat={lat:.2f}&lon={lon:.2f}"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NS_TRIPS_URL = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips"

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # not %a: locale-dependent

DAILY_FIELDS = (
    "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,"
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
                if resp.status == 200:
                    return resp.read()
                last = FetchError(f"HTTP {resp.status}")
        except Exception as exc:  # network errors, HTTP errors, timeouts
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(1)
    raise FetchError(f"{url.split('?')[0]}: {last}")


# --- URLs -------------------------------------------------------------------

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


# --- Parsers ------------------------------------------------------------------

def parse_om_hourly(doc: dict) -> list[float]:
    temps = (doc.get("hourly") or {}).get("temperature_2m")
    if not isinstance(temps, list):
        return []
    return [_num(v, 0.0) for v in temps[:24]]


def parse_om_daily(doc: dict) -> list[DayForecast]:
    d = doc.get("daily") or {}

    def col(name: str) -> list:
        v = d.get(name)
        return v if isinstance(v, list) else []

    tmax, tmin = col("temperature_2m_max"), col("temperature_2m_min")
    cols = {k: col(k) for k in (
        "precipitation_probability_max", "weather_code", "sunshine_duration",
        "daylight_duration", "precipitation_sum", "precipitation_hours", "snowfall_sum",
        "time", "sunrise", "sunset", "wind_speed_10m_max", "wind_gusts_10m_max",
        "apparent_temperature_max", "uv_index_max")}

    def at(name: str, i: int):
        c = cols[name]
        return c[i] if i < len(c) else None

    out = []
    for i in range(min(7, len(tmax))):
        # (int)(float) in C: truncation toward zero, null reads as 0.
        cat, sunny = daily_category(
            _int(at("weather_code", i), 0),
            _num(at("precipitation_sum", i), 0.0),
            # Firmware reads this with `| 0`, which gives 0 for a JSON float
            # such as 3.0. Kept as-is for the exact port; see server/README.md.
            _int(at("precipitation_hours", i), 0),
            _num(at("snowfall_sum", i), 0.0),
            _num(at("sunshine_duration", i), 0.0) / 3600.0,
            _num(at("daylight_duration", i), 0.0) / 3600.0,
        )
        uv = min(max(_num(at("uv_index_max", i), 0.0), 0.0), 15.0)
        sr, ss = at("sunrise", i), at("sunset", i)
        if i == 0:
            day_name = "Today"
        else:
            date = at("time", i)
            try:
                day_name = DAY_ABBR[datetime.strptime(date[:10], "%Y-%m-%d").weekday()]
            except (TypeError, ValueError):
                day_name = "?"
        out.append(DayForecast(
            day_name=day_name,
            temp_max=int(_num(tmax[i], 0.0)),
            temp_min=int(_num(tmin[i] if i < len(tmin) else None, 0.0)),
            rain_prob=_int(at("precipitation_probability_max", i), 0),
            category=cat,
            sunny_variant=sunny,
            sunrise=sr[11:16] if isinstance(sr, str) and len(sr) >= 16 else "",
            sunset=ss[11:16] if isinstance(ss, str) and len(ss) >= 16 else "",
            wind_max_kmh=int(_num(at("wind_speed_10m_max", i), 0.0)),
            gust_max_kmh=int(_num(at("wind_gusts_10m_max", i), 0.0)),
            feels_max=int(_num(at("apparent_temperature_max", i), 0.0)),
            uv_max_x10=int(uv * 10.0),
        ))
    return out


def parse_br_stations(doc: dict) -> list[dict]:
    stations = (doc.get("actual") or {}).get("stationmeasurements")
    return stations if isinstance(stations, list) else []


def parse_br_rain(text: str) -> list[tuple[float, str]]:
    """24 lines of "VVV|HH:MM" at 5-minute steps; VVV maps to mm/h by
    Buienradar's published formula 10^((VVV - 109) / 32)."""
    out = []
    for line in text.splitlines():
        if len(out) == 24:
            break
        pipe = line.find("|")
        if pipe <= 0 or len(line) - pipe < 6:
            continue
        digits = ""
        for ch in line[:pipe].strip():
            if not ch.isdigit():
                break
            digits += ch
        v = int(digits) if digits else 0
        mmh = 0.0 if v <= 0 else 10 ** ((v - 109) / 32)
        out.append((mmh, line[pipe + 1:pipe + 6]))
    return out


def parse_trips(doc: dict, origin: str, max_count: int = 6) -> list[Departure]:
    trips = doc.get("trips")
    if not isinstance(trips, list):
        return []
    out: list[Departure] = []
    for trip in trips:
        if len(out) >= max_count:
            break
        legs = trip.get("legs") if isinstance(trip, dict) else None
        if not isinstance(legs, list) or not legs:
            continue
        first, last = legs[0], legs[-1]
        fo = first.get("origin") or {}
        planned = fo.get("plannedDateTime")
        actual = fo.get("actualDateTime")
        track = fo.get("plannedTrack")
        if not isinstance(planned, str) or len(planned) < 16:
            continue
        has_actual = isinstance(actual, str) and len(actual) >= 16
        cancelled = bool(first.get("cancelled")) or bool(first.get("partCancelled"))

        delay = calculate_delay(planned, actual) if not cancelled and has_actual else 0
        if delay < 1:
            delay = 0

        ld = last.get("destination") or {}
        arr = ld.get("actualDateTime")
        if not (isinstance(arr, str) and len(arr) >= 16):
            arr = ld.get("plannedDateTime")
        uni_arr = arr[11:16] if isinstance(arr, str) and len(arr) >= 16 else ""

        if len(legs) == 1:
            transfer = Transfer.OK
        elif bool(last.get("cancelled")) or bool(last.get("partCancelled")):
            transfer = Transfer.CANCELLED
        else:
            lo = last.get("origin") or {}
            lp, la = lo.get("plannedDateTime"), lo.get("actualDateTime")
            late = (isinstance(lp, str) and isinstance(la, str) and len(lp) >= 16 and len(la) >= 16
                    and calculate_delay(lp, la) >= 5)
            transfer = Transfer.LATE if late else Transfer.OK

        out.append(Departure(
            origin=origin,
            time=(actual if has_actual else planned)[11:16],
            track=(track if isinstance(track, str) else "?")[:5],
            delay_min=delay,
            cancelled=cancelled,
            uni_arr=uni_arr,
            transfer=transfer,
            planned_iso=planned[:25],
            leg_count=min(len(legs), 255),
        ))
    return out


# --- Fetchers -----------------------------------------------------------------
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

    def __init__(self, folder) -> None:
        self.folder = folder

    def get(self, key: str) -> bytes:
        path = self.folder / FIXTURE_FILES[key]
        if not path.exists():
            raise FetchError(f"no fixture {path.name}")
        return path.read_bytes()


class RecordingFetcher:
    """Fetches live and saves each response body into a fixture folder."""

    def __init__(self, inner, folder) -> None:
        self.inner, self.folder = inner, folder

    def get(self, key: str) -> bytes:
        body = self.inner.get(key)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / FIXTURE_FILES[key]).write_bytes(body)
        return body


def parse(key: str, body: bytes):
    if key == "om_hourly":
        return parse_om_hourly(json.loads(body))
    if key == "om_daily":
        return parse_om_daily(json.loads(body))
    if key == "br_feed":
        return parse_br_stations(json.loads(body))
    if key == "br_rain":
        return parse_br_rain(body.decode("utf-8", "replace"))
    if key == "ns_ctr":
        return parse_trips(json.loads(body), "CTR")
    if key == "ns_hs":
        return parse_trips(json.loads(body), "HS")
    raise KeyError(key)
