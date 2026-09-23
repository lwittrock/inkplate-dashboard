"""The gate an automatic deploy must pass before a release goes live.

    python -m screen.selftest

Uses only what the server has (Pillow, no pytest, no network): renders every
recorded fixture and a synthetic screen that exercises every section in both
panel modes, checks every wire format survives compression at its exact size,
and asks the schedule for a whole week of wakes. Exit code
0 means the release may be switched to; anything else keeps the old one.
"""

import json
import sys
import zlib
from datetime import datetime, timedelta

from . import frames, render
from .collect import Collector
from .config import SERVER_DIR, Settings
from .model import (Category, Departure, DayForecast, RainSample, Snapshot, Transfer,
                    WeatherNow)
from .schedule import plan
from .sources import FixtureFetcher


def _check_frame(name: str, snap: Snapshot) -> None:
    for fmt, raw, size in (("g4z", frames.pack_grey(render.render_grey(snap)), frames.GREY_BYTES),
                           ("m1z", frames.pack_mono(render.render_mono(snap)), frames.MONO_BYTES)):
        if len(raw) != size:
            raise AssertionError(f"{name} {fmt}: frame is {len(raw)} bytes, not {size}")
        if zlib.decompress(frames.compress(raw)) != raw:
            raise AssertionError(f"{name} {fmt}: compression does not round-trip")
    if not any(frames.pack_mono(render.render_mono(snap))):
        raise AssertionError(f"{name}: frame is blank")


def _synthetic(hour: int) -> Snapshot:
    now = datetime(2026, 9, 23, hour, 5)
    day = lambda name, cat, sunny: DayForecast(name, 18, 9, 17, cat, sunny, "07:31", "19:39",
                                               30.0, 45.0, 3.0)
    dep = lambda origin, hhmm, **kw: Departure(
        origin=origin, planned=now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:])),
        departs=now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:])),
        arrives=now.replace(hour=int(hhmm[:2]) + 1), track="5b", delay_min=kw.get("delay", 0),
        cancelled=kw.get("cancelled", False), transfer=kw.get("transfer", Transfer.OK), leg_count=2)
    return Snapshot(
        now=now,
        weather=WeatherNow(temp=-3.7, wind_kmh=42.4, category=Category.RAIN_HEAVY, wind_bearing=225),
        rain=[RainSample(0.3 * i, f"{hour:02d}:{i * 5 % 60:02d}") for i in range(12)],
        hourly=[12 - abs(12 - i) * 0.4 for i in range(24)],
        forecast=[day(n, c, s) for n, c, s in [
            ("Today", Category.RAIN, False), ("Thu", Category.CLEAR, False),
            ("Fri", Category.DRIZZLE, True), ("Sat", Category.SNOW, True),
            ("Sun", Category.FOG, False), ("Mon", Category.THUNDERSTORM, False),
            ("Tue", Category.PARTLY_CLOUDY, False)]],
        departures=[dep("CTR", f"{hour:02d}:10", delay=12, transfer=Transfer.LATE),
                    dep("HS", f"{hour:02d}:14"), dep("CTR", f"{hour:02d}:40", cancelled=True)],
        battery_v=3.9, firmware="selftest")


def main() -> int:
    settings = Settings.from_env()
    checked = []

    for folder in sorted((SERVER_DIR / "tests" / "fixtures").glob("*/")):
        now = datetime.fromisoformat(json.loads((folder / "meta.json").read_text())["now"])
        snap = Collector(settings, FixtureFetcher(folder)).snapshot(now)
        _check_frame(f"fixture {folder.name}", snap)
        checked.append(folder.name)

    for hour in (9, 21):
        _check_frame(f"synthetic {hour}:05", _synthetic(hour))
    _check_frame("empty", Snapshot(now=datetime(2026, 9, 23, 12, 0)))

    t = datetime(2026, 9, 21, 0, 0, 20)
    while t < datetime(2026, 9, 28):
        s = plan(t, failing=False, ota_sent_for=None).sleep_s
        if not 300 <= s <= 28800:
            raise AssertionError(f"schedule: {t} gives {s} s")
        t += timedelta(minutes=7)

    print(f"selftest: ok (fixtures: {', '.join(checked) or 'none'}; synthetic; empty; schedule)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"selftest: FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
