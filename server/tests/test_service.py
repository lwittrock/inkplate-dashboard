"""The service end to end: a real HTTP server on a free port, the wall1
fixture as the upstream APIs, a settable clock, and a fake forwarder."""

import dataclasses
import json
import threading
import urllib.error
import urllib.request
import zlib
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from screen.config import Settings
from screen.service import Service, make_handler
from screen.sources import FixtureFetcher
from screen.telemetry import Forwarder, Report, State, battery_percent

FIXTURE = Path(__file__).parent / "fixtures" / "wall1"


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def svc(tmp_path):
    sent = []
    forwarder = Forwarder(ha_webhook_url="http://ha.test/api/webhook/secret",
                          hc_device_url="https://hc-ping.test/device",
                          send=lambda url, body: sent.append((url, body)))
    clock = Clock(datetime(2026, 9, 23, 21, 39, 30))
    service = Service(Settings.from_env(), FixtureFetcher(FIXTURE), forwarder,
                      tmp_path / "state.json", clock=clock)
    service.render_now()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{path}") as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    yield service, clock, get, sent, forwarder
    server.shutdown()


def test_a_normal_evening_wake(svc):
    service, clock, get, sent, forwarder = svc
    status, headers, body = get("/v1/screen?batt=3.91&fw=v2026.10.02-01&rssi=-61&wake=3&fail=0")
    assert status == 200 and len(body) == 60_000
    assert headers["X-Sleep"] == str(31 * 60)       # 21:39:30 -> 22:10:30, weekday off-peak
    assert headers["X-Refresh"] == "full"            # first time this firmware is seen
    assert "X-Ota" not in headers

    status, headers, _ = get("/v1/screen?batt=3.90&fw=v2026.10.02-01&wake=5&fail=0")
    assert headers["X-Refresh"] == "partial"

    forwarder.q.join()
    payload = next(body for url, body in sent if "webhook" in url)
    assert payload["battery_v"] == 3.91 and payload["battery_pct"] == 65
    assert payload["seen_at"] == "2026-09-23T21:39:30+02:00"
    assert ("https://hc-ping.test/device", None) in sent
    assert State.load(service.state_path).last == {"batt": 3.9, "fw": "v2026.10.02-01", "wake": 5, "fail": 0}


def test_after_failures_the_device_gets_a_full_redraw(svc):
    _, _, get, _, _ = svc
    get("/v1/screen?fw=a&wake=5")
    _, headers, _ = get("/v1/screen?fw=a&wake=5&fail=2")
    assert headers["X-Refresh"] == "full"


def test_the_night_wake_gets_204_and_one_ota_hint(svc):
    _, clock, get, _, _ = svc
    clock.t = datetime(2026, 9, 24, 0, 5, 40)
    status, headers, body = get("/v1/screen?fw=a&wake=40")
    assert (status, body, headers["X-Ota"]) == (204, b"", "1")
    assert int(headers["X-Sleep"]) == 6 * 3600 + 24 * 60 + 50
    status, headers, _ = get("/v1/screen?fw=a&wake=41")
    assert status == 204 and "X-Ota" not in headers
    status, _, body = get("/v1/screen?fw=a&wake=42&fail=3")
    assert status == 200 and len(body) == 60_000


def test_garbage_in_the_query_string_is_ignored(svc):
    _, _, get, _, _ = svc
    status, _, body = get("/v1/screen?batt=lots&wake=x&rssi=&fw=" + "v" * 80)
    assert status == 200 and len(body) == 60_000
    r = Report.from_query({"batt": ["99"], "wake": ["x"], "fw": ["v" * 80]})
    assert (r.batt, r.wake, len(r.fw)) == (None, None, 40)


def test_preview_status_and_unknown_paths(svc):
    _, _, get, _, _ = svc
    status, headers, body = get("/preview.png")
    assert status == 200 and body.startswith(b"\x89PNG")
    get("/v1/screen?batt=3.8")
    status, _, body = get("/status")
    assert json.loads(body)["last_report"] == {"batt": 3.8}
    assert get("/nope")[0] == 404


def test_battery_percent_follows_the_lipo_curve():
    assert battery_percent(4.25) == 100
    assert battery_percent(3.84) == 50
    assert battery_percent(3.86) in (57, 58)
    assert battery_percent(3.0) == 0
    assert battery_percent(None) is None and battery_percent(0.0) is None


def test_the_device_gets_greyscale_when_it_offers_it(svc):
    _, _, get, _, _ = svc
    status, headers, body = get("/v1/screen?fmt=g4z,m1z&fw=a&wake=1")
    assert (status, headers["X-Format"]) == (200, "g4z")
    frame = zlib.decompress(body)
    assert len(frame) == 240_000 and len(body) < 40_000
    assert max(b >> 4 for b in frame) <= 7 and max(b & 15 for b in frame) <= 7   # levels 0..7 only


def test_screen_format_mono_sends_compressed_1_bit(svc):
    service, _, get, _, _ = svc
    service.settings = dataclasses.replace(service.settings, screen_format="mono")
    status, headers, body = get("/v1/screen?fmt=g4z,m1z&fw=a&wake=1")
    assert (headers["X-Format"], len(zlib.decompress(body))) == ("m1z", 60_000)


def test_a_request_without_fmt_still_gets_the_raw_frame(svc):
    _, _, get, _, _ = svc
    status, headers, body = get("/v1/screen?fw=a&wake=1")
    assert "X-Format" not in headers and len(body) == 60_000
