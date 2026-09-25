from datetime import datetime, timedelta

import pytest

from screen.headline import DAYS, greeting
from screen.model import Category, DayForecast, HourForecast, Station
from screen.render import Canvas
from screen.weather import DayCounts, category_from_icon, count_day, day_category, hour_sky, pick_current

HOME = (52.0799, 4.3133)
NOW = datetime(2026, 9, 23, 14, 0)


def test_icon_categories():
    assert category_from_icon("a") == Category.CLEAR
    assert category_from_icon("cc") == Category.OVERCAST
    assert category_from_icon("c") == Category.OVERCAST
    assert category_from_icon("z") == Category.OVERCAST


@pytest.mark.parametrize("code, cat", [
    ("q", Category.RAIN),               # zwaar bewolkt en regen: was heavy rain
    ("m", Category.DRIZZLE),            # zwaar bewolkt met wat lichte regen: was heavy rain
    ("l", Category.RAIN),               # was thunderstorm
    ("t", Category.SNOW),               # zware sneeuwval: was missing
    ("j", Category.PARTLY_CLOUDY),      # opklaringen en hoge bewolking: was clear
    ("f", Category.SHOWERS), ("h", Category.SHOWERS), ("k", Category.SHOWERS),
])
def test_buienradar_codes_as_python_buienradar_reads_them(code, cat):
    assert category_from_icon(code) == cat


def test_hour_sky_needs_sunshine_and_cloud_to_agree():
    assert hour_sky(3600, 30) == "clear"
    assert hour_sky(3600, 98) == "partly"       # sun through thin high cloud
    assert hour_sky(0, 30) == "partly"
    assert hour_sky(14 * 60, 80) == "overcast"
    assert hour_sky(15 * 60, 100) == "partly"
    assert hour_sky(45 * 60, 49) == "clear"


def sky(clear=0, partly=0, overcast=0, **kw):
    return DayCounts(clear=clear, partly=partly, overcast=overcast, **kw)


def test_the_sky_needs_more_than_half_the_daylight_hours():
    assert day_category(sky(clear=7, partly=5)) == Category.CLEAR
    assert day_category(sky(clear=6, overcast=6)) == Category.PARTLY_CLOUDY
    assert day_category(sky(overcast=5, partly=3)) == Category.OVERCAST
    assert day_category(sky(clear=4, partly=4, overcast=4)) == Category.PARTLY_CLOUDY
    assert day_category(sky()) == Category.PARTLY_CLOUDY


@pytest.mark.parametrize("counts, cat", [
    # 1. Snow beats everything, from two hours.
    (sky(clear=12, snow=2, wet=6, rain_mm=12, thunder=3), Category.SNOW),
    (sky(clear=12, snow=1), Category.CLEAR),
    # 2. Thunder, from two hours, beats a wet day.
    (sky(partly=12, thunder=2, wet=6, rain_mm=12), Category.THUNDERSTORM),
    (sky(clear=12, thunder=1), Category.CLEAR),
    # 3. Five wet hours make a rainy day, whatever the sky; the amount says how rainy.
    (sky(clear=12, wet=5, rain_mm=2.9), Category.DRIZZLE),
    (sky(clear=12, wet=5, rain_mm=3.0), Category.RAIN),
    (sky(clear=12, wet=5, rain_mm=10.0), Category.RAIN_HEAVY),
    # 4. Fog, from three hours, but not over a rainy day.
    (sky(partly=12, fog=3), Category.FOG),
    (sky(partly=12, fog=2), Category.PARTLY_CLOUDY),
    (sky(partly=12, fog=6, wet=5, rain_mm=4), Category.RAIN),
    (sky(partly=12, fog=3, wet=3, rain_mm=4), Category.FOG),
    # 5. Two to four wet hours on an overcast day are rain.
    (sky(overcast=7, partly=5, wet=2, rain_mm=0.8), Category.DRIZZLE),
    (sky(overcast=7, partly=5, wet=4, rain_mm=3.5), Category.RAIN),
    # 6. ... and on any other day, sun and showers.
    (sky(clear=7, partly=5, wet=2, rain_mm=0.8), Category.SHOWERS),
    (sky(overcast=6, partly=6, wet=4, rain_mm=12), Category.SHOWERS),
    # 7. One wet hour doesn't count.
    (sky(clear=12, wet=1, rain_mm=8.1), Category.CLEAR),
])
def test_day_rules_first_match_wins(counts, cat):
    assert day_category(counts) == cat


def hour(t, mm=0.0, snow=0.0, sun=0.0, cloud=100.0, code=3):
    return HourForecast(time=t, temp=15.0, code=code, precip_mm=mm, snow_cm=snow, sun_s=sun, cloud_pct=cloud)


def test_the_window_is_07_to_21_and_the_sky_counts_daylight_only():
    day = datetime(2026, 9, 29)
    hours = [hour(day + timedelta(hours=h), mm=2.0, sun=3600, cloud=10) for h in range(24)]
    c = count_day(hours, sunrise=day.replace(hour=7, minute=40), sunset=day.replace(hour=19, minute=20))
    # Stamps 08:00-21:00 are the 14 hours from 07:00; rain before 07:00 or after 21:00 is outside.
    assert (c.wet, c.rain_mm) == (14, 28.0)
    # Daylight: 08:00 (07:40-08:00, 20 min) is out; 20:00 (19:00-19:20) is out; 09:00-19:00 are in.
    assert c.clear == 11


def test_thresholds_are_inclusive_and_traces_dont_count():
    day = datetime(2026, 1, 6)
    hours = [hour(day.replace(hour=9), mm=0.3), hour(day.replace(hour=10), mm=0.29),
             hour(day.replace(hour=11), snow=0.1), hour(day.replace(hour=12), snow=0.07),
             hour(day.replace(hour=13), code=45), hour(day.replace(hour=14), code=48),
             hour(day.replace(hour=15), code=95), hour(day.replace(hour=16), code=99)]
    c = count_day(hours, day.replace(hour=8, minute=50), day.replace(hour=16, minute=40))
    assert (c.wet, c.snow, c.fog, c.thunder) == (1, 1, 2, 2)


def station(name, lat, lon, code, temp=15.0, observed=datetime(2026, 9, 23, 13, 50)):
    return Station(name=name, lat=lat, lon=lon, observed=observed, icon_code=code,
                   temp=temp, wind_ms=5.0, bearing=270)


def pick(stations):
    return pick_current(stations, NOW, *HOME, stale_min=60, consensus_km=30, max_candidates=6)


def test_nearest_outlier_is_outvoted_and_readings_come_from_the_closest_winner():
    # 2026-05-25: Voorschoten said overcast while every neighbour said clear.
    w = pick([
        station("Voorschoten", 52.12, 4.43, "p", temp=14.0),
        station("Rotterdam", 51.95, 4.45, "a", temp=18.0),
        station("Hoek van Holland", 51.98, 4.12, "a", temp=17.0),
        station("Schiphol", 52.30, 4.77, "a", temp=16.0),
    ])
    assert w.category == Category.CLEAR
    assert w.temp == 18.0                     # Rotterdam: closest station that voted clear
    assert round(w.wind_kmh) == 18            # 5 m/s


def test_a_tie_goes_to_the_closest_station():
    w = pick([station("Near", 52.09, 4.32, "p", temp=11.0), station("Far", 52.2, 4.4, "a")])
    assert (w.category, w.temp) == (Category.OVERCAST, 11.0)


def test_stale_stations_are_skipped():
    w = pick([
        station("Old", 52.08, 4.31, "a", observed=datetime(2026, 9, 23, 12, 30)),
        station("Fresh", 52.2, 4.4, "p", temp=12.5),
    ])
    assert (w.category, w.temp) == (Category.OVERCAST, 12.5)


def test_no_station_in_range_falls_back_to_the_nearest():
    w = pick([station("Far", 53.2, 5.8, "q", temp=9.0), station("Farther", 53.4, 6.2, "a")])
    assert (w.category, w.temp) == (Category.RAIN, 9.0)


def forecast(cat=Category.CLEAR, tmax=20, feels=20, wind=10.0, gust=20.0, uv=4.0):
    return DayForecast("Today", tmax, 10, feels, cat, "07:30", "19:40", wind, gust, uv)


def test_greeting_rules():
    wed = datetime(2026, 9, 23, 9, 0)
    assert greeting(datetime(2026, 12, 25, 9, 0), forecast(), None) == "First Day of Christmas"
    assert greeting(wed, None, None) == "Wednesday"
    assert greeting(wed, forecast(), None) == "Sunny Wednesday"
    assert greeting(wed, forecast(tmax=26, uv=6.0), None) == "Glorious Wednesday"
    assert greeting(wed, forecast(tmax=7), None) == "Crisp Wednesday"
    assert greeting(wed, forecast(Category.PARTLY_CLOUDY, tmax=17), None) == "Mixed Wednesday"
    assert greeting(wed, forecast(gust=80), None) == "Stormy Wednesday"
    assert greeting(wed, forecast(feels=4, gust=45), None) == "Cold Wednesday"
    assert greeting(wed, forecast(feels=29, gust=45), None) == "Hot Wednesday"
    assert greeting(wed, forecast(gust=45), None) == "Windy Wednesday"
    assert greeting(wed, forecast(Category.RAIN), None) == "Wet Wednesday"
    assert greeting(wed, forecast(Category.RAIN, gust=45), None) == "Wet and windy Wednesday"
    assert greeting(wed, forecast(Category.RAIN, wind=26.0), None) == "Wet and windy Wednesday"
    assert greeting(wed, forecast(Category.SHOWERS), None) == "Showery Wednesday"
    assert greeting(wed, forecast(Category.SHOWERS, gust=45), None) == "Wet and windy Wednesday"


def test_rainy_day_clearing_up_in_the_evening_takes_current_conditions():
    rainy = forecast(Category.RAIN)
    assert greeting(datetime(2026, 9, 23, 15, 59), rainy, Category.CLEAR) == "Wet Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), rainy, Category.CLEAR) == "Sunny Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), rainy, Category.OVERCAST) == "Wet Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), forecast(Category.RAIN, gust=45), Category.CLEAR) \
        == "Windy Wednesday"


def test_every_greeting_fits_left_of_the_sun_arc():
    c = Canvas(mono=False)
    width = lambda s: c.width(s, 34, 650)       # the masthead's greeting type
    longest = max([f"Wet and windy {d}" for d in DAYS] + [f"Glorious {d}" for d in DAYS]
                  + ["Second Day of Christmas"], key=width)
    assert width(longest) <= 540   # x=40 to the arc's left end at x=598, minus 18 px
