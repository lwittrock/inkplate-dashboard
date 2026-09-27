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
from screen.history import History
from screen.model import Category
from screen.nowlog import NowLog
from screen.service import Service, make_handler
from screen.sources import FixtureFetcher
from screen.schedule import TZ, plan
from screen.telemetry import Forwarder, Report, State, battery_percent, due

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
                      tmp_path / "state.json", clock=clock, now_log=NowLog(tmp_path / "now.jsonl"),
                      history=History(tmp_path / "history.db"))
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
    assert payload["next_wake_at"] == "2026-09-23T22:10:30+02:00"      # the X-Sleep above
    assert payload["late_after"] == "2026-09-23T22:20:30+02:00"        # 10 minutes later
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


def test_the_report_says_when_the_device_is_late():
    seen = datetime(2026, 9, 24, 0, 5, 40, tzinfo=TZ)
    # The night sleep, 6 h 24 min 50 s: 5% of it (19 min 14 s) beats 10 minutes.
    assert due(seen, 23090) == (datetime(2026, 9, 24, 6, 30, 30, tzinfo=TZ),
                                datetime(2026, 9, 24, 6, 49, 44, tzinfo=TZ))
    # The night of the autumn switch is an hour longer in real seconds, as the schedule gives it.
    seen = datetime(2026, 10, 25, 0, 5, 40, tzinfo=TZ)
    sleep_s = plan(seen.replace(tzinfo=None), failing=False, ota_sent_for=None).sleep_s
    wake, _ = due(seen, sleep_s)
    assert (sleep_s, wake.isoformat()) == (26690, "2026-10-25T06:30:30+01:00")


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
    st = json.loads(body)
    assert (st["last_report"], st["battery_pct"]) == ({"batt": 3.8}, 40)
    assert st["now"]["shown"] == st["now"]["vote"] == "overcast"     # 21:39: no sun to overrule it
    assert st["now"]["why"].startswith("overcast (vote; vote overcast; ")
    assert get("/nope")[0] == 404


def test_data_serves_the_last_render_with_offsets(svc):
    service, _, get, _, _ = svc
    status, headers, body = get("/data")
    d = json.loads(body)
    assert (status, headers["Content-Type"]) == (200, "application/json")
    assert d["rendered_at"] == "2026-09-23T21:39:30+02:00"
    assert [(t["origin"], t["departs"], t["arrives"]) for t in d["trains"]["departures"]] == [
        ("CTR", "2026-09-23T21:49:00+02:00", "2026-09-23T23:10:00+02:00"),
        ("CTR", "2026-09-23T22:49:00+02:00", "2026-09-24T00:10:00+02:00")]    # after midnight
    assert d["trains"]["ok"] is True and d["trains"]["departures"][0]["transfer"] == "ok"
    w = d["weather"]
    assert w["now"]["category"] == "overcast"            # what the wall shows, as in /status
    assert len(w["hours"]) == 24 and w["hours"][0]["at"] == "2026-09-23T21:00:00+02:00"
    assert [x["date"] for x in w["days"]][:2] == ["2026-09-23", "2026-09-24"]
    assert w["days"][0]["name"] == "Today"
    assert {x["category"] for x in w["days"]} <= {c.name.lower() for c in Category}
    assert {x["category"] for x in w["hours"]} <= {c.name.lower() for c in Category} - {"showers"}
    assert w["hours"][0]["daylight"] is False and any(x["daylight"] for x in w["hours"])
    assert all(r["time"][2] == ":" for r in w["rain"])

    service.snapshot = None
    assert get("/data")[0] == 503


def test_every_centraal_trip_goes_into_the_history(svc):
    service, clock, get, _, _ = svc
    status, _, body = get("/history/trains/rows?days=2")
    rows = json.loads(body)
    # wall1's five Centraal trips, the wall's two and the three it leaves out, and the
    # four HS trips of the first render, which always asks HS.
    assert status == 200 and [(r["origin"], r["planned"][11:16]) for r in rows if r["origin"] == "CTR"] == [
        ("CTR", "21:49"), ("CTR", "22:49"), ("CTR", "04:44"), ("CTR", "05:49"), ("CTR", "06:19")]
    assert json.loads(get("/status")[2])["history"] == {"trains": 9, "since": "2026-09-23T21:49:00+02:00"}

    clock.t = datetime(2026, 9, 24, 12, 0)
    status, _, body = get("/history/trains?days=nonsense")
    s = json.loads(body)
    assert (status, s["days"], s["late_min"]) == (200, 30, 5)
    # Seen at 21:39 only: 21:49 counts (10 minutes before), 22:49 does not.
    assert [(r["time"], r["trains"]) for r in s["by_departure"]] == [("21:49", 1)]

    service.history = None
    assert get("/history/trains")[0] == 503 and json.loads(get("/status")[2])["history"] is None


def test_now_log_serves_each_renders_choice(svc):
    service, clock, get, _, _ = svc
    clock.t = datetime(2026, 9, 23, 21, 44, 30)
    service.render_now()
    status, headers, body = get("/now-log?days=1")
    records = [json.loads(line) for line in body.decode().splitlines()]
    assert (status, headers["Content-Type"]) == (200, "application/x-ndjson")
    assert [r["t"] for r in records] == ["2026-09-23T21:39:30", "2026-09-23T21:44:30"]
    assert records[0]["stations"][0]["voting"] is True
    assert get("/now-log?days=nonsense")[0] == 200


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
