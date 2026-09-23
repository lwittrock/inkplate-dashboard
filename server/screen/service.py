"""The HTTP service the Inkplate talks to.

    python -m screen.service                       # live APIs, port 8088
    python -m screen.service --fixture wall1       # replay a fixture
    python -m screen.service --at 2026-09-23T23:20 # pretend it is that time (clock runs on)

Endpoints:
    GET /v1/screen?fmt=g4z,m1z&batt=..&fw=..&rssi=..&wake=..&fail=..&awake_ms=..&wifi_ms=..
        200 + a frame in the format named by X-Format (see frames.py), or 204
        at night (keep the panel, just sleep).
        Headers: X-Sleep (seconds), X-Format, X-Refresh (full|partial), X-Ota (1 = check now).
    GET /preview.png   the current frame, for a browser on the laptop
    GET /status        last render and last device report, as JSON

The contract is described in docs/server-rendering-design.md. Change it only
backward-compatibly: the service deploys in minutes, the device after midnight.
"""

import argparse
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from . import frames, render2, schedule
from .collect import Collector
from .config import SERVER_DIR, Settings, load_env_file
from .sources import FixtureFetcher, LiveFetcher
from .telemetry import Forwarder, Report, State

log = logging.getLogger("screen")
TZ = ZoneInfo("Europe/Amsterdam")


def wall_clock() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


class Service:
    def __init__(self, settings: Settings, fetcher, forwarder: Forwarder, state_path: Path,
                 clock: Callable[[], datetime] = wall_clock, hc_render_url: str = "") -> None:
        self.settings = settings
        self.collector = Collector(settings, fetcher)
        self.forwarder = forwarder
        self.state_path = state_path
        self.state = State.load(state_path)
        self.clock = clock
        self.hc_render_url = hc_render_url
        self.lock = threading.Lock()
        self.bodies: dict[str | None, bytes] = {}   # by format; None = raw 1-bit
        self.png: bytes = b""
        self.rendered_at: datetime | None = None

    # --- rendering ------------------------------------------------------------

    def render_now(self) -> None:
        now = self.clock()
        snap = self.collector.snapshot(now)
        with self.lock:
            snap.battery_v = self.state.battery_v
            if self.settings.show_version_footer:
                snap.firmware = self.state.firmware
        grey = render2.render_grey(snap, crisp_small=self.settings.crisp_small)
        mono = render2.render_mono(snap)
        mono_raw = frames.pack_mono(mono)
        bodies = {"g4z": frames.compress(frames.pack_grey(grey)),
                  "m1z": frames.compress(mono_raw),
                  None: mono_raw}
        buf = io.BytesIO()
        (grey if self.settings.screen_format == "grey" else mono.convert("L")).save(buf, format="PNG")
        with self.lock:
            self.bodies, self.png, self.rendered_at = bodies, buf.getvalue(), now
        log.info("rendered %s: %d departures, weather %s", f"{now:%H:%M}", len(snap.departures),
                 "ok" if snap.weather else "missing")

    def render_loop(self) -> None:
        """Render on every 5-minute slot, except at night, when nobody looks
        and the device keeps its last picture."""
        while True:
            now = self.clock()
            slot = schedule.slot_at_or_after(now + timedelta(seconds=1))
            time.sleep(max((slot - now).total_seconds(), 1))
            ok = True
            if not schedule.in_night(slot.time()):
                try:
                    self.render_now()
                except Exception:
                    log.exception("render failed, keeping the previous frame")
                    ok = False
            self.forwarder.ping(self.hc_render_url, ok)

    # --- the device's request ---------------------------------------------------

    def handle_screen(self, query: dict[str, list[str]]) -> tuple[int, dict[str, str], bytes]:
        now = self.clock()
        report = Report.from_query(query)
        offered = [f.strip() for f in query.get("fmt", [""])[0].split(",") if f.strip()]
        fmt = frames.choose(offered, self.settings.screen_format)
        failing = bool(report.fail)
        with self.lock:
            new_firmware = report.fw is not None and report.fw != self.state.firmware
            p = schedule.plan(now, failing=failing, ota_sent_for=self.state.ota_night())
            if p.ota:
                self.state.ota_sent_for = schedule.night_key(now).isoformat()
            self.state.last_seen = now.isoformat()
            self.state.last = {k: v for k, v in vars(report).items() if v is not None}
            try:
                self.state.save(self.state_path)
            except OSError as exc:
                log.warning("could not save state: %s", exc)
            frame = self.bodies.get(fmt, b"")

        headers = {"X-Sleep": str(p.sleep_s)}
        if p.ota:
            headers["X-Ota"] = "1"
        if p.draw and frame:
            headers["X-Refresh"] = schedule.refresh_mode(wake=report.wake, failing=failing,
                                                         new_firmware=new_firmware)
            if fmt:
                headers["X-Format"] = fmt
            status, body = 200, frame
        else:
            status, body = 204, b""
        log.info("device: %s %s %s %dB sleep=%ss%s%s batt=%s fail=%s", status, fmt or "raw",
                 headers.get("X-Refresh", "-"), len(body), p.sleep_s, " ota" if p.ota else "", " new-fw" if new_firmware else "",
                 report.batt, report.fail)
        # Forwarding is queued, and the queue is served on another thread.
        self.forwarder.device_seen(report, now.replace(tzinfo=TZ))
        return status, headers, body

    def status(self) -> dict:
        with self.lock:
            return {
                "rendered_at": self.rendered_at.isoformat() if self.rendered_at else None,
                "last_seen": self.state.last_seen,
                "last_report": self.state.last,
                "ota_sent_for": self.state.ota_sent_for,
            }


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "inkplate-screen"
        sys_version = ""

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path == "/v1/screen":
                status, headers, body = service.handle_screen(parse_qs(url.query))
                self._reply(status, "application/octet-stream", body, headers)
            elif url.path == "/preview.png":
                with service.lock:
                    png = service.png
                self._reply(200, "image/png", png)
            elif url.path == "/status":
                self._reply(200, "application/json", json.dumps(service.status(), indent=1).encode())
            else:
                self._reply(404, "text/plain", b"not found\n")

        def _reply(self, status: int, ctype: str, body: bytes, headers: dict | None = None) -> None:
            self.send_response(status)
            if status != 204:
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            if status != 204:
                self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            # Query strings are fine to log, but keep the journal to one line per request.
            log.debug("%s %s", self.address_string(), fmt % args)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", help="replay tests/fixtures/NAME instead of the live APIs")
    ap.add_argument("--at", help="start the clock at this local time (YYYY-MM-DDTHH:MM)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env_file()
    env = os.environ.get
    settings = Settings.from_env()

    clock = wall_clock
    if args.at:
        offset = datetime.fromisoformat(args.at) - wall_clock()
        clock = lambda: wall_clock() + offset  # noqa: E731

    fetcher = (FixtureFetcher(SERVER_DIR / "tests" / "fixtures" / args.fixture)
               if args.fixture else LiveFetcher(settings))
    state_dir = Path(env("STATE_DIRECTORY", str(SERVER_DIR / "state")))   # systemd sets STATE_DIRECTORY
    state_dir.mkdir(parents=True, exist_ok=True)
    forwarder = Forwarder(ha_webhook_url=env("HA_WEBHOOK_URL", ""), hc_device_url=env("HC_PING_INKPLATE", ""))
    service = Service(settings, fetcher, forwarder, state_dir / "state.json", clock=clock,
                      hc_render_url=env("HC_PING_RENDER", ""))

    service.render_now()   # never serve "no frame yet"
    threading.Thread(target=service.render_loop, name="render", daemon=True).start()
    bind, port = env("BIND", "0.0.0.0"), int(env("PORT", "8088"))
    log.info("listening on %s:%d", bind, port)
    ThreadingHTTPServer((bind, port), make_handler(service)).serve_forever()


if __name__ == "__main__":
    main()
