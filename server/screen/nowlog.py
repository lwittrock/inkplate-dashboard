"""NOW's choices, one JSON line per render, kept in the state directory.

The record (model.NowChoice.record) holds everything needed to re-judge a
choice later against KNMI's measured hours: docs/weather-categories.md,
"NOW: sun through high cloud". The file survives restarts, whether or not
the journal does, and /now-log serves it.
"""

import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

KEEP = timedelta(days=14)       # ~170 KB a day


class NowLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.trimmed_on: date | None = None

    def append(self, record: dict, now: datetime) -> None:
        """Add a record; once a day, drop those older than KEEP. A failed
        write is logged, never raised: the render matters more."""
        with self.lock:
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, separators=(",", ":")) + "\n")
                if self.trimmed_on != now.date():
                    self._keep_since(now - KEEP)
                    self.trimmed_on = now.date()
            except OSError as exc:
                log.warning("could not write %s: %s", self.path.name, exc)

    def since(self, t: datetime) -> bytes:
        """The records from `t` on, as JSON lines."""
        with self.lock:
            return "".join(line + "\n" for line in self._lines_since(t)).encode()

    def _lines_since(self, t: datetime) -> list[str]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        start = t.isoformat(timespec="seconds")
        # Records start {"t":"YYYY-MM-DDTHH:MM:SS", and ISO times sort as text.
        return [line for line in lines if line[6:25] >= start]

    def _keep_since(self, t: datetime) -> None:
        kept = self._lines_since(t)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        tmp.replace(self.path)
