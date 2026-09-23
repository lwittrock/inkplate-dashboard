"""Render design mockups side by side: the current design and render2, in
grey and 1-bit, for a recorded evening and a synthetic rainy morning.

    python tools/mockups.py OUT_DIR
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from screen import render, render2
from screen.collect import Collector
from screen.config import Settings
from screen.model import (Category, Departure, DayForecast, RainSample, Snapshot, Transfer,
                          WeatherNow)
from screen.sources import FixtureFetcher

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def evening() -> Snapshot:
    folder = FIXTURES / "wall1"
    now = datetime.fromisoformat(json.loads((folder / "meta.json").read_text())["now"])
    snap = Collector(Settings.from_env(), FixtureFetcher(folder)).snapshot(now)
    snap.battery_v = 3.93
    return snap


def morning() -> Snapshot:
    now = datetime(2026, 9, 24, 8, 4)
    t = lambda hm, d=0: datetime(2026, 9, 24 + d, int(hm[:2]), int(hm[3:]))
    def dep(origin, time, arr, **kw):
        return Departure(origin=origin, planned=t(kw.pop("planned", time)), departs=t(time), arrives=t(arr),
                         track=kw.pop("track", "5b"), delay_min=kw.pop("delay", 0),
                         cancelled=kw.pop("cancelled", False), transfer=kw.pop("transfer", Transfer.OK),
                         leg_count=2)
    rain = [0.0, 0.0, 0.1, 0.3, 0.6, 1.2, 2.1, 3.4, 4.8, 5.6, 5.1, 4.0, 3.1, 2.2, 1.6, 1.1,
            0.8, 0.5, 0.3, 0.2, 0.1, 0.0, 0.0, 0.0]
    labels = [f"{(8 * 60 + 5 + 5 * i) // 60:02d}:{(5 + 5 * i) % 60:02d}" for i in range(24)]
    days = [("Today", 14, 9, Category.RAIN, False), ("Fri", 16, 10, Category.DRIZZLE, True),
            ("Sat", 18, 11, Category.PARTLY_CLOUDY, False), ("Sun", 21, 12, Category.CLEAR, False),
            ("Mon", 19, 13, Category.OVERCAST, False), ("Tue", 15, 10, Category.RAIN_HEAVY, False),
            ("Wed", 13, 7, Category.PARTLY_CLOUDY, False)]
    return Snapshot(
        now=now,
        weather=WeatherNow(temp=11.4, wind_kmh=27.7, category=Category.RAIN, wind_bearing=235,
                           feels=8.3, gust_kmh=52.2),
        rain=[RainSample(v, l) for v, l in zip(rain, labels)],
        hourly=[11.2, 11.6, 12.3, 13.1, 13.8, 14.1, 13.6, 13.0, 12.1, 11.4, 10.8, 10.3,
                9.9, 9.6, 9.4, 9.1, 8.9, 8.7, 8.6, 8.8, 9.6, 10.9, 12.2, 13.0],
        forecast=[DayForecast(n, hi, lo, hi - 3, cat, sunny, "07:33", "19:37", 30.0, 55.0, 2.0)
                  for n, hi, lo, cat, sunny in days],
        departures=[dep("CTR", "08:10", "09:20"),
                    dep("HS", "08:14", "09:26", track="12", planned="08:14"),
                    dep("CTR", "08:52", "10:05", planned="08:40", delay=12, transfer=Transfer.LATE)],
        battery_v=3.71)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, snap in (("evening", evening()), ("morning", morning())):
        old = render.render(snap).to_image().convert("L")
        grey = render2.render_grey(snap)
        mono = render2.render_mono(snap).convert("L")
        for tag, img in (("old", old), ("grey", grey), ("mono", mono)):
            img.save(out / f"{name}-{tag}.png")
        sheet = Image.new("L", (800 * 3 + 40, 600), 200)
        for k, img in enumerate((old, mono, grey)):
            sheet.paste(img, (k * 820, 0))
        sheet.save(out / f"{name}-compare.png")
        print(out / f"{name}-compare.png")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
