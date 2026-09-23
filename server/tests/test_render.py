from datetime import datetime

import pytest

from screen import gfx
from screen.gfx import BLACK, Canvas
from screen.model import Category, Departure, DayForecast, RainSample, Snapshot, Transfer, WeatherNow
from screen.render import render


def test_frame_format():
    c = Canvas()
    c.pixel(0, 0)
    c.pixel(799, 599)
    frame = c.to_frame()
    assert len(frame) == 60_000
    assert frame[0] == 0x80 and frame[-1] == 0x01 and frame[1] == 0


def test_line_and_circle_match_adafruit():
    c = Canvas()
    c.line(0, 0, 4, 2)
    assert [(x, y) for y in range(3) for x in range(5) if c.px[y * 800 + x]] == \
        [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2)]
    c = Canvas()
    c.fill_circle(10, 10, 2)
    rows = ["".join("#" if c.px[y * 800 + x] else "." for x in range(8, 13)) for y in range(8, 13)]
    assert rows == [".###.", "#####", "#####", "#####", ".###."]


def test_text_bounds_follow_the_glyph_table():
    c = Canvas()
    c.set_font("Inter_Bold9pt7b")
    _, w, h, xa, xo, yo = gfx.font("Inter_Bold9pt7b").glyphs[ord("H") - 32]
    assert c.text_bounds("H", 100, 50) == (100 + xo, 50 + yo, w, h)
    assert c.text_width("HH") - c.text_width("H") == xa


def dep(origin, time, arr, **kw):
    t = datetime.fromisoformat(f"2026-09-23T{time}")
    return Departure(origin=origin, planned=t, departs=t,
                     arrives=datetime.fromisoformat(f"2026-09-23T{arr}"),
                     track=kw.pop("track", "5b"), delay_min=kw.pop("delay", 0),
                     cancelled=kw.pop("cancelled", False), transfer=kw.pop("transfer", Transfer.OK),
                     leg_count=2)


def busy_snapshot(hour=17):
    days = [DayForecast(n, 18, 9, 17, cat, sunny, "07:31", "19:39", 20.0, 45.0, 3.0)
            for n, cat, sunny in [("Today", Category.RAIN, False), ("Thu", Category.CLEAR, False),
                                  ("Fri", Category.DRIZZLE, True), ("Sat", Category.SNOW, True),
                                  ("Sun", Category.FOG, False), ("Mon", Category.THUNDERSTORM, False),
                                  ("Tue", Category.RAIN_HEAVY, False)]]
    rain = [RainSample(0.0 if i < 4 else 0.3 * i, f"{hour + (i * 5) // 60:02d}:{(i * 5) % 60:02d}")
            for i in range(24)]
    return Snapshot(
        now=datetime(2026, 9, 23, hour, 5),
        weather=WeatherNow(temp=-3.7, wind_kmh=42.4, category=Category.RAIN_HEAVY, wind_bearing=225),
        rain=rain, hourly=[12 - abs(12 - i) * 0.4 for i in range(24)], forecast=days,
        departures=[dep("CTR", "17:10", "18:20", delay=12, transfer=Transfer.LATE),
                    dep("HS", "17:14", "18:12", track="12"),
                    dep("CTR", "17:40", "18:50", cancelled=True)],
        battery_v=3.9, firmware="v2026.10.02-01")


@pytest.mark.parametrize("hour", [9, 21])
def test_every_section_renders_into_a_frame(hour):
    frame = render(busy_snapshot(hour)).to_frame()
    assert len(frame) == 60_000


def test_empty_snapshot_renders_the_fallbacks():
    c = render(Snapshot(now=datetime(2026, 9, 23, 12, 0)))
    assert len(c.to_frame()) == 60_000
    assert any(c.px)
