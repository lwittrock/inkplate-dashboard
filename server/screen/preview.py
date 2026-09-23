"""Render the screen on the laptop.

    python -m screen.preview                    # live APIs -> preview.png
    python -m screen.preview --record NAME      # live, and save the responses as fixture NAME
    python -m screen.preview --fixture NAME     # replay fixture NAME at the time it was recorded
    python -m screen.preview --fixture NAME --at 2026-09-23T17:40

Settings come from the environment and server/local.env (NS_API_KEY,
LATITUDE, LONGITUDE, ...). Draws the current design in greyscale; --mono for
the 1-bit panel mode, --old for the original port of the firmware's design.
Output: preview.png, enlarged 2x for viewing, and frame.bin, the frame the
device would receive (uncompressed).
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from . import frames, render, render2
from .collect import Collector
from .config import SERVER_DIR, Settings, load_env_file
from .sources import FixtureFetcher, LiveFetcher, RecordingFetcher

FIXTURES = SERVER_DIR / "tests" / "fixtures"
TZ = ZoneInfo("Europe/Amsterdam")


def local_now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--fixture", help="replay tests/fixtures/NAME")
    src.add_argument("--record", help="fetch live and save as tests/fixtures/NAME")
    ap.add_argument("--at", help="render as if it were this local time (YYYY-MM-DDTHH:MM)")
    ap.add_argument("--battery", type=float, help="battery voltage to show in the footer")
    ap.add_argument("--out", type=Path, default=SERVER_DIR / "preview.png")
    ap.add_argument("--scale", type=int, default=2)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--mono", action="store_true", help="the 1-bit panel mode")
    mode.add_argument("--old", action="store_true", help="the first design, ported from the firmware")
    ap.add_argument("--crisp", action="store_true", help="greyscale with crisp small text")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env_file()
    settings = Settings.from_env()

    if args.fixture:
        folder = FIXTURES / args.fixture
        meta = json.loads((folder / "meta.json").read_text())
        now = datetime.fromisoformat(meta["now"])
        fetcher = FixtureFetcher(folder)
    else:
        now = local_now()
        fetcher = LiveFetcher(settings)
        if args.record:
            folder = FIXTURES / args.record
            fetcher = RecordingFetcher(fetcher, folder)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "meta.json").write_text(json.dumps({"now": now.isoformat()}) + "\n")
    if args.at:
        now = datetime.fromisoformat(args.at)

    snap = Collector(settings, fetcher).snapshot(now)
    snap.battery_v = args.battery
    if args.old:
        canvas = render.render(snap)
        img, frame = canvas.to_image(), canvas.to_frame()
    elif args.mono:
        img = render2.render_mono(snap)
        frame = frames.pack_mono(img)
    else:
        img = render2.render_grey(snap, crisp_small=args.crisp)
        frame = frames.pack_grey(img)

    if args.scale > 1:
        img = img.resize((img.width * args.scale, img.height * args.scale), Image.NEAREST)
    img.save(args.out)
    args.out.with_name("frame.bin").write_bytes(frame)
    print(f"{args.out} ({now:%Y-%m-%d %H:%M}, {len(snap.departures)} departures, "
          f"weather {'ok' if snap.weather else 'missing'}, "
          f"{len(snap.forecast)} days, {len(snap.hourly)} hours, {len(snap.rain)} rain samples)")


if __name__ == "__main__":
    main()
