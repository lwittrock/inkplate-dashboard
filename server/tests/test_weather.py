from datetime import datetime

from screen.gfx import Canvas
from screen.headline import DAYS, greeting
from screen.model import Category, DayForecast, Station
from screen.weather import category_from_icon, daily_category, pick_current

HOME = (52.0799, 4.3133)
NOW = datetime(2026, 9, 23, 14, 0)


def test_icon_categories():
    assert category_from_icon("a") == Category.CLEAR
    assert category_from_icon("cc") == Category.OVERCAST
    assert category_from_icon("c") == Category.OVERCAST
    assert category_from_icon("m") == Category.RAIN_HEAVY
    assert category_from_icon("z") == Category.OVERCAST


def test_daily_category_thresholds():
    day = dict(api_code=3, precip_sum=0.0, precip_hours=0.0, snowfall_sum=0.0, daylight_h=12.0)
    assert daily_category(**day, sunshine_h=8.0) == (Category.CLEAR, False)
    assert daily_category(**day, sunshine_h=5.0) == (Category.PARTLY_CLOUDY, False)
    assert daily_category(**day, sunshine_h=1.0) == (Category.OVERCAST, False)
    assert daily_category(**{**day, "api_code": 45}, sunshine_h=8.0) == (Category.FOG, False)
    assert daily_category(**{**day, "precip_sum": 2.0}, sunshine_h=8.0) == (Category.PARTLY_CLOUDY, False)
    assert daily_category(**{**day, "precip_sum": 2.0}, sunshine_h=1.0) == (Category.DRIZZLE, False)
    assert daily_category(**{**day, "precip_sum": 12.0}, sunshine_h=7.0) == (Category.RAIN_HEAVY, True)
    assert daily_category(**{**day, "precip_hours": 3.0}, sunshine_h=1.0) == (Category.DRIZZLE, False)
    assert daily_category(**{**day, "snowfall_sum": 2.0}, sunshine_h=7.0) == (Category.SNOW, True)


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
    w = pick([station("Far", 53.2, 5.8, "h", temp=9.0), station("Farther", 53.4, 6.2, "a")])
    assert (w.category, w.temp) == (Category.RAIN, 9.0)


def forecast(cat=Category.CLEAR, tmax=20, feels=20, wind=10.0, gust=20.0, uv=4.0):
    return DayForecast("Today", tmax, 10, feels, cat, False, "07:30", "19:40", wind, gust, uv)


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


def test_rainy_day_clearing_up_in_the_evening_takes_current_conditions():
    rainy = forecast(Category.RAIN)
    assert greeting(datetime(2026, 9, 23, 15, 59), rainy, Category.CLEAR) == "Wet Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), rainy, Category.CLEAR) == "Sunny Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), rainy, Category.OVERCAST) == "Wet Wednesday"
    assert greeting(datetime(2026, 9, 23, 16, 0), forecast(Category.RAIN, gust=45), Category.CLEAR) \
        == "Windy Wednesday"


def test_every_greeting_fits_left_of_the_sun_arc():
    c = Canvas()
    c.set_font("Inter_Bold18pt7b")
    longest = max([f"Wet and windy {d}" for d in DAYS] + [f"Glorious {d}" for d in DAYS]
                  + ["Second Day of Christmas"], key=c.text_width)
    assert c.text_width(longest) <= 564   # x=40 to the arc at x=620, minus 16 px
