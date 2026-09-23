"""What the device reports on each request, where it is kept, and where it goes.

Every /v1/screen request carries the device's battery, firmware and signal
in its query string. The service stores the latest report (state.json) and,
after replying, passes it on: to a Home Assistant webhook (options B) and as
a healthchecks.io ping (option A). Forwarding runs on a worker thread so the
device's radio never waits for it.
"""

import json
import logging
import queue
import threading
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# LiPo open-circuit voltage to charge, a common single-cell table.
# The curve is flat in the middle: 3.84 V is still half full.
_LIPO = [(4.20, 100), (4.15, 95), (4.11, 90), (4.08, 85), (4.02, 80), (3.98, 75),
         (3.95, 70), (3.91, 65), (3.87, 60), (3.85, 55), (3.84, 50), (3.82, 45),
         (3.80, 40), (3.79, 35), (3.77, 30), (3.75, 25), (3.73, 20), (3.71, 15),
         (3.69, 10), (3.61, 5), (3.27, 0)]


def battery_percent(v: float | None) -> int | None:
    if v is None or v <= 0:
        return None           # no reading, e.g. on USB power without a battery
    if v >= _LIPO[0][0]:
        return 100
    for (v_hi, p_hi), (v_lo, p_lo) in zip(_LIPO, _LIPO[1:]):
        if v >= v_lo:
            return round(p_lo + (v - v_lo) / (v_hi - v_lo) * (p_hi - p_lo))
    return 0


@dataclass
class Report:
    """One request's query string. Anything missing or malformed is None."""
    batt: float | None = None
    fw: str | None = None
    rssi: int | None = None
    wake: int | None = None
    fail: int | None = None
    awake_ms: int | None = None
    wifi_ms: int | None = None

    @classmethod
    def from_query(cls, q: dict[str, list[str]]) -> "Report":
        def one(name, conv):
            try:
                return conv(q[name][0])
            except (KeyError, IndexError, ValueError):
                return None
        batt = one("batt", float)
        fw = one("fw", str)
        return cls(
            batt=batt if batt is not None and 0 <= batt < 10 else None,
            fw=fw[:40] if fw else None,
            rssi=one("rssi", int), wake=one("wake", int), fail=one("fail", int),
            awake_ms=one("awake_ms", int), wifi_ms=one("wifi_ms", int))


@dataclass
class State:
    """Survives restarts, so a deploy doesn't forget the device."""
    last_seen: str | None = None                 # ISO local time
    last: dict = field(default_factory=dict)     # the last Report
    ota_sent_for: str | None = None              # night_key of the last OTA hint

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text())
            return cls(**{k: raw.get(k) for k in ("last_seen", "last", "ota_sent_for")
                          if raw.get(k) is not None})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1) + "\n")
        tmp.replace(path)

    @property
    def firmware(self) -> str | None:
        return self.last.get("fw")

    @property
    def battery_v(self) -> float | None:
        return self.last.get("batt")

    def ota_night(self) -> date | None:
        return date.fromisoformat(self.ota_sent_for) if self.ota_sent_for else None


def ha_payload(report: Report, seen: datetime) -> dict:
    return {
        "battery_v": report.batt,
        "battery_pct": battery_percent(report.batt),
        "firmware": report.fw,
        "rssi": report.rssi,
        "wake": report.wake,
        "fail": report.fail,
        "awake_ms": report.awake_ms,
        "wifi_ms": report.wifi_ms,
        "seen_at": seen.isoformat(timespec="seconds"),
    }


def http_send(url: str, body: dict | None = None) -> None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=10):
        pass


class Forwarder:
    """Sends reports and pings one at a time on a worker thread. A failure is
    logged and dropped: the next wake brings a fresh report anyway."""

    def __init__(self, *, ha_webhook_url: str = "", hc_device_url: str = "", send=http_send) -> None:
        self.ha_webhook_url = ha_webhook_url
        self.hc_device_url = hc_device_url
        self.send = send
        self.q: queue.Queue = queue.Queue(maxsize=100)
        threading.Thread(target=self._run, name="forwarder", daemon=True).start()

    def device_seen(self, report: Report, seen: datetime) -> None:
        if self.ha_webhook_url:
            self._put(self.ha_webhook_url, ha_payload(report, seen))
        if self.hc_device_url:
            self._put(self.hc_device_url, None)

    def ping(self, url: str, ok: bool = True) -> None:
        """healthchecks.io: GET the ping URL, or its /fail variant."""
        if url:
            self._put(url if ok else url.rstrip("/") + "/fail", None)

    def _put(self, url: str, body: dict | None) -> None:
        try:
            self.q.put_nowait((url, body))
        except queue.Full:
            log.warning("forward queue full, dropping %s", url.split("/")[2] if "//" in url else url)

    def _run(self) -> None:
        while True:
            url, body = self.q.get()
            try:
                self.send(url, body)
            except Exception as exc:
                # Log the host only: webhook ids and ping URLs are secrets.
                log.warning("forward to %s failed: %s", url.split("/")[2] if "//" in url else "?", exc)
            finally:
                self.q.task_done()
