from datetime import datetime

from screen.headline import greeting
from screen.model import Category, DayForecast
from screen.weather import (category_from_icon, daily_category, icon_code_from_url,
                            pick_current)

HOME = (52.0799, 4.3133)
NOW = datetime(2026, 9, 23, 14, 0)


def test_icon_codes():
    url = "https://cdn.buienradar.nl/resources/images/icons/weather/30x30/{}.png"
    assert icon_code_from_url(url.format("aa")) == "a"
    assert icon_code_from_url(url.format("AA")) == "a"
    assert icon_code_from_url(url.format("cc")) == "cc"
    assert icon_code_from_url(url.format("j")) == "j"
    assert icon_code_from_url(url.format("abcd")) == ""
    assert icon_code_from_url(None) == ""
    assert category_from_icon("a") == Category.CLEAR
    assert category_from_icon("cc") == Category.OVERCAST
    assert category_from_icon("m") == Category.RAIN_HEAVY
    assert category_from_icon("z") == Category.OVERCAST


def test_daily_category_thresholds():
    day = dict(api_code=3, precip_sum=0.0, precip_hours=0, snowfall_sum=0.0, daylight_h=12.0)
    assert daily_category(**day, sunshine_h=8.0) == (Category.CLEAR, False)
    assert daily_category(**day, sunshine_h=5.0) == (Category.PARTLY_CLOUDY, False)
    assert daily_category(**day, sunshine_h=1.0) == (Category.OVERCAST, False)
    assert daily_category(**{**day, "api_code": 45}, sunshine_h=8.0) == (Category.FOG, False)
    assert daily_category(**{**day, "precip_sum": 2.0}, sunshine_h=8.0) == (Category.PARTLY_CLOUDY, False)
    assert daily_category(**{**day, "precip_sum": 2.0}, sunshine_h=1.0) == (Category.DRIZZLE, False)
    assert daily_category(**{**day, "precip_sum": 12.0}, sunshine_h=7.0) == (Category.RAIN_HEAVY, True)
    assert daily_category(**{**day, "precip_hours": 3}, sunshine_h=1.0) == (Category.DRIZZLE, False)


def station(name, lat, lon, code, temp=15.0, ts="2026-09-23T13:50:00"):
    return {"stationname": name, "lat": lat, "lon": lon, "temperature": temp, "windspeed": 5.0,
            "winddirectiondegrees": 270, "timestamp": ts,
            "iconurl": f"https://x/30x30/{code}.png"}


def pick(stations):
    return pick_current(stations, NOW, *HOME, stale_min=60, consensus_km=30, max_candidates=6)


def test_nearest_outlier_is_outvoted_and_readings_come_from_the_closest_winner():
    # 2026-05-25: Voorschoten said overcast while every neighbour said clear.
    w = pick([
        station("Voorschoten", 52.12, 4.43, "pp", temp=14.0),
        station("Rotterdam", 51.95, 4.45, "aa", temp=18.0),
        station("Hoek van Holland", 51.98, 4.12, "aa", temp=17.0),
        station("Schiphol", 52.30, 4.77, "aa", temp=16.0),
    ])
    assert w.category == Category.CLEAR
    assert w.temp == 18.0                     # Rotterdam: closest station that voted clear
    assert round(w.wind_kmh) == 18            # 5 m/s


def test_stale_and_unusable_stations_are_skipped():
    w = pick([
        station("Old", 52.08, 4.31, "aa", ts="2026-09-23T12:30:00"),
        {"stationname": "No icon", "lat": 52.08, "lon": 4.31, "temperature": 1.0},
        station("Fresh", 52.2, 4.4, "pp", temp=12.5),
    ])
    assert (w.category, w.temp) == (Category.OVERCAST, 12.5)


def test_no_station_in_range_falls_back_to_the_nearest():
    w = pick([station("Far", 53.2, 5.8, "h", temp=9.0), station("Farther", 53.4, 6.2, "a")])
    assert (w.category, w.temp) == (Category.RAIN, 9.0)


def forecast(cat=Category.CLEAR, tmax=20, feels=20, wind=10, gust=20, uv=40):
    return DayForecast("Today", tmax, 10, 0, cat, False, "07:30", "19:40", wind, gust, feels, uv)


def width(s):
    return len(s) * 20


def test_greeting_rules():
    wed = datetime(2026, 9, 23, 9, 0)
    assert greeting(datetime(2026, 12, 25, 9, 0), forecast(), None, width) == "First Day of Christmas"
    assert greeting(wed, None, None, width) == "Wednesday"
    assert greeting(wed, forecast(), None, width) == "Sunny Wednesday"
    assert greeting(wed, forecast(tmax=26, uv=60), None, width) == "Glorious Wednesday"
    assert greeting(wed, forecast(Category.PARTLY_CLOUDY, tmax=17), None, width) == "Mixed Wednesday"
    assert greeting(wed, forecast(gust=80), None, width) == "Stormy Wednesday"
    assert greeting(wed, forecast(feels=4, gust=45), None, width) == "Cold Wednesday"
    assert greeting(wed, forecast(gust=45), None, width) == "Windy Wednesday"
    # As in the firmware, notable wind always claims the "Windy" override first,
    # so the "Wet and windy" combo, which needs no override, never appears.
    assert greeting(wed, forecast(Category.RAIN, gust=45), None, width) == "Windy Wednesday"
    assert greeting(wed, forecast(Category.RAIN), None, width) == "Wet Wednesday"


def test_rainy_day_clearing_up_in_the_evening_takes_current_conditions():
    rainy = forecast(Category.RAIN)
    assert greeting(datetime(2026, 9, 23, 15, 59), rainy, Category.CLEAR, width) == "Wet Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), rainy, Category.CLEAR, width) == "Sunny Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), rainy, Category.OVERCAST, width) == "Wet Wednesday"
