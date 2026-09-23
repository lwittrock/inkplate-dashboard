"""The masthead's editorial greeting ("Bright Tuesday", "Wet and windy Friday")."""

from datetime import datetime

from .model import RAIN_FAMILY, Category, DayForecast

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# (month, day) -> text, shown instead of the weather greeting.
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


def base_adjective(cat: Category, temp_max: int, uv_max: float) -> str:
    if cat == Category.CLEAR:
        if 25 <= temp_max < 28 and uv_max >= 5.0:
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


def greeting(now: datetime, today: DayForecast | None, current: Category | None) -> str:
    """`today` is None without a daily forecast, `current` None without
    current conditions. The longest result, "Wet and windy Wednesday",
    fits the masthead (tests/test_weather.py checks it)."""
    special = SPECIALS.get((now.month, now.day))
    if special:
        return special

    day = DAYS[now.weekday()]
    if today is None:
        return day

    base = today.category
    # From 16:00, a rainy day that is now clearly clearing up takes the
    # current conditions: the only place live weather reaches the headline.
    if now.hour >= 16 and current in (Category.CLEAR, Category.PARTLY_CLOUDY) and base in RAIN_FAMILY:
        base = current

    windy = today.gust_max_kmh >= 40 or today.wind_max_kmh >= 25
    # First match wins. Temperature beats ordinary wind: it defines a hot,
    # windy day more than the wind does.
    if today.gust_max_kmh >= 75 or today.wind_max_kmh >= 50:
        adj = "Stormy"
    elif today.feels_max <= 0:
        adj = "Freezing"
    elif today.feels_max <= 5:
        adj = "Cold"
    elif today.feels_max >= 28:
        adj = "Hot"
    elif windy and base in RAIN_FAMILY:
        adj = "Wet and windy"
    elif windy:
        adj = "Windy"
    else:
        adj = base_adjective(base, today.temp_max, today.uv_max)
    return f"{adj} {day}"
