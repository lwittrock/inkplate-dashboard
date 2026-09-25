from datetime import datetime

import pytest
from PIL import Image

from screen import frames, render
from screen.model import Category, Departure, DayForecast, RainSample, Snapshot, Transfer, WeatherNow


def dep(origin, time, arr, **kw):
    t = datetime.fromisoformat(f"2026-09-23T{time}")
    return Departure(origin=origin, planned=t, departs=t,
                     arrives=datetime.fromisoformat(f"2026-09-23T{arr}"),
                     track=kw.pop("track", "5b"), delay_min=kw.pop("delay", 0),
                     cancelled=kw.pop("cancelled", False), transfer=kw.pop("transfer", Transfer.OK),
                     leg_count=2)


def busy_snapshot(hour=17):
    days = [DayForecast(n, 18, 9, 17, cat, "07:31", "19:39", 20.0, 45.0, 3.0)
            for n, cat in [("Today", Category.RAIN), ("Thu", Category.CLEAR), ("Fri", Category.SHOWERS),
                           ("Sat", Category.SNOW), ("Sun", Category.FOG), ("Mon", Category.THUNDERSTORM),
                           ("Tue", Category.RAIN_HEAVY)]]
    rain = [RainSample(0.0 if i < 4 else 0.3 * i, f"{hour + (i * 5) // 60:02d}:{(i * 5) % 60:02d}")
            for i in range(24)]
    return Snapshot(
        now=datetime(2026, 9, 23, hour, 5),
        weather=WeatherNow(temp=-3.7, wind_kmh=42.4, category=Category.RAIN_HEAVY, wind_bearing=225,
                           feels=-8.1, gust_kmh=61.0),
        rain=rain, hourly=[12 - abs(12 - i) * 0.4 for i in range(24)], forecast=days,
        departures=[dep("CTR", "17:10", "18:20", delay=12, transfer=Transfer.LATE),
                    dep("HS", "17:14", "18:12", track="12"),
                    dep("CTR", "17:40", "18:50", cancelled=True)],
        battery_v=3.9, firmware="v2026.10.02-01")


@pytest.mark.parametrize("hour", [5, 9, 21])
def test_every_section_renders_in_both_panel_modes(hour):
    snap = busy_snapshot(hour)
    grey = render.render_grey(snap)
    crisp = render.render_grey(snap, crisp_small=True)
    mono = render.render_mono(snap)
    assert grey.size == crisp.size == mono.size == (800, 600)
    panel_levels = {round(i * 255 / 7) for i in range(8)}
    assert set(grey.getdata()) <= panel_levels and set(crisp.getdata()) <= panel_levels
    assert mono.mode == "1"
    assert len(frames.pack_grey(grey)) == frames.GREY_BYTES
    assert len(frames.pack_mono(mono)) == frames.MONO_BYTES


def test_empty_snapshot_renders_the_fallbacks():
    img = render.render_grey(Snapshot(now=datetime(2026, 9, 23, 12, 0)))
    assert min(img.getdata()) == 0      # something was drawn


def test_frame_packing():
    mono = Image.new("1", (800, 600), 1)
    mono.putpixel((0, 0), 0)
    mono.putpixel((799, 599), 0)
    packed = frames.pack_mono(mono)
    assert packed[0] == 0x80 and packed[-1] == 0x01 and packed[1] == 0     # 1 = black, MSB first
    grey = Image.new("L", (800, 600), 255)
    grey.putpixel((0, 0), 0)
    grey.putpixel((1, 0), round(3 * 255 / 7))
    assert frames.pack_grey(grey)[:2] == bytes([0x03, 0x77])               # left pixel in the high nibble


def test_night_before_sunrise_and_after_sunset():
    fc = busy_snapshot().forecast
    assert render.is_night(Snapshot(now=datetime(2026, 9, 23, 6, 40), forecast=fc))
    assert not render.is_night(Snapshot(now=datetime(2026, 9, 23, 12, 0), forecast=fc))
    assert render.is_night(Snapshot(now=datetime(2026, 9, 23, 19, 40), forecast=fc))


@pytest.mark.parametrize("volts", [None, 0.0, 3.0, 3.3, 3.5, 3.6, 3.62, 3.65, 3.7, 4.05, 4.3])
def test_footer_draws_any_battery_reading(volts):
    # 24 September 2026: a flat battery (5% or less) made the fill narrower than a
    # pixel, Pillow raised, and the service crash-looped at start.
    snap = busy_snapshot()
    snap.battery_v = volts
    render.render_grey(snap)
    render.render_grey(snap, crisp_small=True)
    render.render_mono(snap)
