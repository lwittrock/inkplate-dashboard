"""The masthead's editorial greeting ("Bright Tuesday", "Wet and windy Friday").

Ported from makeGreeting in C_Display.ino.
"""

from datetime import datetime

from .model import RAIN_FAMILY, Category, DayForecast

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# (month, day) -> text. First match wins.
SPECIALS = {
    (1, 1): "New year",
    (3, 20): "First day of spring",
    (4, 27): "Koningsdag",
    (5, 5): "Liberation Day",
    (6, 21): "Midsummer",
    (9, 22): "First day of autumn",
    (12, 21): "Midwinter",
    (12, 24): "Christmas Eve",
    (12, 25): "First Day of Christmas",
    (12, 26): "Second Day of Christmas",
    (12, 31): "New Year's Eve",
}

# Widest greeting that fits left of the sun arc: a 580 px slot minus 16 px.
MAX_WIDTH = 564


def base_adjective(cat: Category, temp_max: int, uv_max_x10: int) -> str:
    if cat == Category.CLEAR:
        if 25 <= temp_max < 28 and uv_max_x10 >= 50:
            return "Glorious"
        if temp_max <= 8:
            return "Crisp"
        return "Sunny"
    if cat == Category.PARTLY_CLOUDY:
        return "Bright" if temp_max >= 18 else "Mixed"
    return {
        Category.OVERCAST: "Grey",
        Category.DRIZZLE: "Drizzly",
        Category.RAIN: "Wet",
        Category.RAIN_HEAVY: "Soaking",
        Category.FOG: "Foggy",
        Category.SNOW: "Snowy",
        Category.THUNDERSTORM: "Stormy",
    }.get(cat, "Quiet")


def greeting(now: datetime, today: DayForecast | None, current: Category | None, text_width) -> str:
    """`today` is None without a daily forecast; `current` is None without
    current conditions. `text_width(s)` measures s in the greeting's font."""
    special = SPECIALS.get((now.month, now.day))
    if special:
        return special

    day = DAYS[now.weekday()]
    if today is None:
        return day

    base = today.category
    # From 16:00, a rainy day that is now clearly clearing up takes the
    # current conditions: the only place live weather reaches the headline.
    if now.hour >= 16 and current is not None and base in RAIN_FAMILY and \
            current in (Category.CLEAR, Category.PARTLY_CLOUDY):
        base = current

    adj = base_adjective(base, today.temp_max, today.uv_max_x10)

    notable_wind = today.gust_max_kmh >= 40 or today.wind_max_kmh >= 25
    override = None
    if today.gust_max_kmh >= 75 or today.wind_max_kmh >= 50:
        override = "Stormy"
    elif today.feels_max <= 0:
        override = "Freezing"
    elif today.feels_max <= 5:
        override = "Cold"
    elif today.feels_max >= 28:
        override = "Hot"
    elif notable_wind:
        override = "Windy"
    if override:
        adj = override

    if not override and base in RAIN_FAMILY and notable_wind:
        combo = f"Wet and windy {day}"
        if text_width(combo) <= MAX_WIDTH:
            return combo
    return f"{adj} {day}"
