import json
from datetime import date, datetime
from pathlib import Path

import pytest

from screen.collect import Collector, hour_now, hours_ahead, week_ahead
from screen.config import Settings
from screen.model import Category, Transfer
from screen.sources import (FixtureFetcher, icon_code_from_url, local_time, parse_br_rain,
                            parse_br_stations, parse_om, parse_trips)

FIXTURES = Path(__file__).parent / "fixtures"
DATA = Path(__file__).parent / "data"


def leg(planned, actual=None, arr_planned=None, arr_actual=None, cancelled=False, track="5"):
    def iso(t):
        return f"2026-09-{'24' if t.startswith('+') else '23'}T{t.lstrip('+')}:00+0200" if t else None
    return {
        "cancelled": cancelled,
        "origin": {"plannedDateTime": iso(planned), "actualDateTime": iso(actual), "plannedTrack": track},
        "destination": {"plannedDateTime": iso(arr_planned), "actualDateTime": iso(arr_actual)},
    }


def test_parse_trips():
    doc = {"trips": [
        {"legs": [leg("08:10", "08:22"), leg("09:05", "09:05", "09:20", "09:21")]},
        {"legs": [leg("08:40", cancelled=True), leg("09:35", None, "09:50")]},
        {"legs": [leg("09:10", "09:10"), leg("10:05", "10:11", "10:20")]},
        {"legs": [leg("23:40"), leg("+00:35", None, "+00:50", cancelled=True)]},
        {"legs": []},
    ]}
    a, b, c, d = parse_trips(doc, "CTR")
    assert (a.time, a.delay_min, a.uni_arr, a.transfer, a.leg_count) == ("08:22", 12, "09:21", Transfer.OK, 2)
    assert a.planned == datetime(2026, 9, 23, 8, 10)
    assert (b.time, b.cancelled, b.delay_min, b.uni_arr) == ("08:40", True, 0, "09:50")
    assert c.transfer == Transfer.LATE           # Breda sprinter 6 minutes late
    assert d.transfer == Transfer.CANCELLED
    assert d.arrives == datetime(2026, 9, 24, 0, 50)


def test_times_convert_to_amsterdam_wall_clock():
    assert local_time("2026-09-23T21:49:00+0200") == datetime(2026, 9, 23, 21, 49)
    assert local_time("2026-09-23T19:49:00+00:00") == datetime(2026, 9, 23, 21, 49)
    assert local_time("2026-09-23T21:20:00") == datetime(2026, 9, 23, 21, 20)
    assert local_time("nonsense") is None


def test_parse_rain():
    rain = parse_br_rain("000|14:05\r\n077|14:10\n109|14:15\nbad line\n")
    assert [s.label for s in rain] == ["14:05", "14:10", "14:15"]
    assert rain[0].mmh == 0.0
    assert round(rain[1].mmh, 3) == 0.1
    assert rain[2].mmh == 1.0


def test_parse_stations():
    url = "https://cdn.buienradar.nl/resources/images/icons/weather/30x30/{}.png"
    doc = {"actual": {"stationmeasurements": [
        {"stationname": "A", "lat": 52.1, "lon": 4.3, "temperature": 17.1, "windspeed": 2.0,
         "winddirectiondegrees": 276, "timestamp": "2026-09-23T21:20:00", "iconurl": url.format("aa")},
        {"stationname": "B", "lat": 52.2, "lon": 4.4, "temperature": 16.0,
         "winddirection": "ZW", "iconurl": url.format("cc")},
        {"stationname": "No icon", "temperature": 15.0},
        {"stationname": "No temperature", "iconurl": url.format("a")},
    ]}}
    a, b = parse_br_stations(doc)
    assert (a.icon_code, a.bearing, a.observed) == ("a", 276, datetime(2026, 9, 23, 21, 20))
    assert (b.icon_code, b.bearing, b.wind_ms) == ("cc", 225, 0.0)
    assert icon_code_from_url(url.format("AA")) == "a"
    assert icon_code_from_url(url.format("abcd")) == ""


def om_doc(offset_h=2, days=("2026-09-23", "2026-09-24")):
    """Two days of hours, labelled with a fixed offset as Open-Meteo does."""
    times = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    return {"utc_offset_seconds": offset_h * 3600,
            "hourly": {"time": times, "temperature_2m": [float(i) for i in range(len(times))],
                       "weather_code": [3] * len(times), "precipitation": [0.0] * len(times),
                       "snowfall": [0.0] * len(times), "sunshine_duration": [3600.0] * len(times),
                       "cloud_cover": [10] * len(times)},
            "daily": {"time": list(days),
                      "temperature_2m_max": [18.7, -0.6], "temperature_2m_min": [11.2, -3.5],
                      "apparent_temperature_max": [17.5, -2.4],
                      "sunrise": [f"{days[0]}T07:31", f"{days[1]}T07:33"],
                      "sunset": [f"{days[0]}T19:39", f"{days[1]}T19:37"],
                      "wind_speed_10m_max": [24.9, 30.0], "wind_gusts_10m_max": [41.0, 50.0],
                      "uv_index_max": [3.45, 1.0]}}


def test_parse_om():
    om = parse_om(om_doc())
    wed, thu = om.days.values()
    assert list(om.days) == [date(2026, 9, 23), date(2026, 9, 24)]
    assert (wed.day_name, thu.day_name) == ("Wed", "Thu")
    assert (wed.temp_max, wed.temp_min, thu.temp_max, thu.temp_min) == (19, 11, -1, -4)
    assert (wed.feels_max, thu.feels_max) == (18, -2)
    assert (wed.sunrise, wed.sunset) == ("07:31", "19:39")
    assert wed.category == Category.CLEAR
    assert (len(om.hours), om.hours[0].time) == (48, datetime(2026, 9, 23, 0, 0))


def test_open_meteo_labels_are_corrected_across_dst():
    """Asked in summer for a day in winter, Open-Meteo still labels it UTC+2."""
    om = parse_om(om_doc(offset_h=2, days=("2026-10-25", "2026-10-26")))
    day = om.days[date(2026, 10, 26)]
    assert (day.sunrise, day.sunset) == ("06:33", "18:37")
    assert om.hours[-1].time == datetime(2026, 10, 26, 22, 0)
    # The autumn switch: 02:00-03:00 happens twice, and every label after it is corrected.
    oct25 = [h.time for h in om.hours if h.time.date() == date(2026, 10, 25)]
    assert oct25[:5] == [datetime(2026, 10, 25, h) for h in (0, 1, 2, 2, 3)]
    assert (len(oct25), oct25[-1]) == (25, datetime(2026, 10, 25, 23, 0))   # a 25-hour day

    # The recording of January asked in September: sunrise is 08:50, not 09:50.
    jan = parse_om(json.loads((DATA / "om_snow_2026_01" / "om.json").read_text()))
    assert jan.days[date(2026, 1, 4)].sunrise == "08:50"


@pytest.mark.parametrize("folder, expected", [
    ("om_week1", {"2026-09-24": Category.PARTLY_CLOUDY, "2026-09-25": Category.CLEAR,
                  "2026-09-26": Category.PARTLY_CLOUDY, "2026-09-27": Category.PARTLY_CLOUDY,
                  "2026-09-28": Category.SHOWERS, "2026-09-29": Category.PARTLY_CLOUDY,
                  "2026-09-30": Category.OVERCAST}),
    ("om_snow_2026_01", {"2026-01-04": Category.RAIN, "2026-01-05": Category.SNOW,
                         "2026-01-06": Category.SHOWERS, "2026-01-07": Category.SNOW,
                         "2026-01-08": Category.SHOWERS}),
])
def test_recorded_weeks_match_the_plan(folder, expected):
    """docs/weather-categories.md, "Results so far"."""
    om = parse_om(json.loads((DATA / folder / "om.json").read_text()))
    assert {d.isoformat(): f.category for d, f in om.days.items()} == expected


def test_the_chart_and_the_week_are_cut_from_one_list_at_any_age():
    om = parse_om(om_doc())
    temps = hours_ahead(om, datetime(2026, 9, 23, 22, 40))
    assert temps[0] == 22.0 and len(temps) == 24
    assert len(hours_ahead(om, datetime(2026, 9, 24, 3, 10))) == 21       # what is left
    assert hours_ahead(om, datetime(2026, 9, 25, 0, 5)) == []

    # NOW's model hour is the one now falls in, stamped at its end.
    assert hour_now(om, datetime(2026, 9, 23, 22, 40)).time == datetime(2026, 9, 23, 23, 0)
    assert hour_now(om, datetime(2026, 9, 25, 9, 0)) is None

    # Just after midnight with yesterday's list: the week starts today, with one day fewer.
    week = week_ahead(om, datetime(2026, 9, 24, 0, 20))
    assert [d.day_name for d in week] == ["Today"]
    assert [d.day_name for d in week_ahead(om, datetime(2026, 9, 23, 8, 0))] == ["Today", "Thu"]
    assert week_ahead(om, datetime(2026, 9, 25, 0, 20)) == []


def test_recorded_evening_shows_both_remaining_trains():
    """wall1: recorded 23 September 2026 at 21:39, when the wall (and the
    exact port) showed one card because of the midnight comparison."""
    folder = FIXTURES / "wall1"
    now = datetime.fromisoformat(json.loads((folder / "meta.json").read_text())["now"])
    snap = Collector(Settings.from_env(), FixtureFetcher(folder)).snapshot(now)
    assert [(d.origin, d.time, d.uni_arr) for d in snap.departures] == \
        [("CTR", "21:49", "23:10"), ("CTR", "22:49", "00:10")]
    assert snap.weather and len(snap.forecast) == 7 and len(snap.hourly) == 24
