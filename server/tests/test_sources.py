from screen.model import Category, Transfer
from screen.sources import parse_br_rain, parse_om_daily, parse_trips


def leg(planned, actual=None, arr_planned=None, arr_actual=None, cancelled=False, track="5"):
    return {
        "cancelled": cancelled,
        "origin": {"plannedDateTime": f"2026-09-23T{planned}:00+0200",
                   "actualDateTime": f"2026-09-23T{actual}:00+0200" if actual else None,
                   "plannedTrack": track},
        "destination": {"plannedDateTime": f"2026-09-23T{arr_planned}:00+0200" if arr_planned else None,
                        "actualDateTime": f"2026-09-23T{arr_actual}:00+0200" if arr_actual else None},
    }


def test_parse_trips():
    doc = {"trips": [
        {"legs": [leg("08:10", "08:22"), leg("09:05", "09:05", "09:20", "09:21")]},
        {"legs": [leg("08:40", cancelled=True), leg("09:35", None, "09:50")]},
        {"legs": [leg("09:10", "09:10"), leg("10:05", "10:11", "10:20")]},
        {"legs": [leg("09:40"), leg("10:35", None, "10:50", cancelled=True)]},
        {"legs": []},
    ]}
    a, b, c, d = parse_trips(doc, "CTR")
    assert (a.time, a.delay_min, a.uni_arr, a.transfer, a.leg_count) == ("08:22", 12, "09:21", Transfer.OK, 2)
    assert (b.time, b.cancelled, b.delay_min, b.uni_arr) == ("08:40", True, 0, "09:50")
    assert c.transfer == Transfer.LATE           # Breda sprinter 6 minutes late
    assert d.transfer == Transfer.CANCELLED
    assert a.planned_iso == "2026-09-23T08:10:00+0200"


def test_parse_rain():
    rain = parse_br_rain("000|14:05\r\n077|14:10\n109|14:15\nbad line\n")
    assert [label for _, label in rain] == ["14:05", "14:10", "14:15"]
    assert rain[0][0] == 0.0
    assert round(rain[1][0], 3) == 0.1
    assert rain[2][0] == 1.0


def test_parse_daily_reads_precipitation_hours_like_the_firmware():
    # Open-Meteo sends precipitation_hours as 12.0; the firmware's `| 0` reads
    # any JSON float as 0, so the ">= 3 hours means drizzle" rule never fires.
    doc = {"daily": {
        "time": ["2026-09-23", "2026-09-24"],
        "temperature_2m_max": [18.7, -0.6], "temperature_2m_min": [11.2, -3.9],
        "precipitation_probability_max": [40, 80], "weather_code": [3, 3],
        "sunshine_duration": [3600.0, 3600.0], "daylight_duration": [43200.0, 43200.0],
        "precipitation_sum": [0.4, 0.4], "precipitation_hours": [12.0, 12],
        "snowfall_sum": [0.0, 0.0],
        "sunrise": ["2026-09-23T07:31", "2026-09-24T07:33"],
        "sunset": ["2026-09-23T19:39", "2026-09-24T19:37"],
        "wind_speed_10m_max": [24.9, 30.0], "wind_gusts_10m_max": [41.0, 50.0],
        "apparent_temperature_max": [17.5, -2.5], "uv_index_max": [3.45, 99.0],
    }}
    today, thu = parse_om_daily(doc)
    assert (today.day_name, thu.day_name) == ("Today", "Thu")
    assert (today.temp_max, today.temp_min, thu.temp_max, thu.temp_min) == (18, 11, 0, -3)
    assert today.category == Category.OVERCAST      # 12.0 hours read as 0
    assert thu.category == Category.DRIZZLE          # an integer 12 would count
    assert (today.sunrise, today.sunset) == ("07:31", "19:39")
    assert (today.uv_max_x10, thu.uv_max_x10) == (34, 150)
    assert (today.feels_max, thu.feels_max) == (17, -2)
