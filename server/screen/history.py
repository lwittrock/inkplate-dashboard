"""Records worth keeping, in one SQLite file in the state directory.

CT 106 is the home's data service, with the wall as one of its consumers
(homeserver-docs, decisions.md, Appendix I). Numbers over time, such as the
temperature, are Home Assistant's to keep; records that only this service
sees, such as each train, are kept here. One table per kind.

The schema is versioned with PRAGMA user_version and grows by MIGRATIONS
only. A file newer than this code switches the history off instead of
touching it, so rolling a release back is safe. A failed write is logged,
never raised: the frame matters more.

The trains table holds one row per train, keyed by origin and planned
departure, updated on every fresh NS answer that lists it: every Centraal
trip to Tilburg Universiteit NS returns, not only the wall's three, after the
same filter_dominated the picker uses. HS trips are kept when fetched, which
is every 45 minutes unless Centraal is disrupted. A row's values are the last
seen, so they are what NS said shortly before the train left, not its
official record. Nothing is fetched at night (23:30-06:30), and nothing is
deleted. Times carry their UTC offset, as in /data.
"""

import logging
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .data import TZ, stamp
from .model import Departure
from .trains import LATE_MIN, filter_dominated, status

log = logging.getLogger(__name__)

MIGRATIONS = {
    1: ["""CREATE TABLE trains (
             origin TEXT NOT NULL,              -- CTR (Den Haag Centraal) or HS (Den Haag HS)
             planned TEXT NOT NULL,             -- planned departure
             departs TEXT NOT NULL,             -- actual or expected departure, as last seen
             delay_min INTEGER NOT NULL,        -- 0 when on time, unknown or cancelled
             cancelled INTEGER NOT NULL,
             arrives_planned TEXT,              -- at Tilburg Universiteit
             arrives TEXT,                      -- actual or expected, as last seen
             transfer TEXT NOT NULL,            -- at Breda: ok, late, cancelled
             track TEXT NOT NULL,
             legs INTEGER NOT NULL,
             first_seen TEXT NOT NULL,
             last_seen TEXT NOT NULL,
             PRIMARY KEY (origin, planned)
           ) WITHOUT ROWID"""],
}
SCHEMA_VERSION = max(MIGRATIONS)

ORIGINS = {"ns_ctr": "CTR", "ns_hs": "HS"}     # source key -> origin
SEEN_BEFORE_LEAVING = timedelta(minutes=10)


def _rank(d: Departure) -> tuple:
    """Among one train's routings, the one a traveller takes: earliest arrival, fewest legs."""
    return (d.arrives is None, d.arrives or datetime.max, d.leg_count)


def _minutes(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return round((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds() / 60)


def counts(row: dict) -> bool:
    """Whether a train's row can speak for how it left: seen within 10 minutes
    of leaving. A night gap or HS's 45-minute fetches would otherwise leave an
    early, rosier answer passing for the last word."""
    return (datetime.fromisoformat(row["last_seen"])
            >= datetime.fromisoformat(row["departs"]) - SEEN_BEFORE_LEAVING)


def disrupted(row: dict) -> bool:
    return status(row["delay_min"], bool(row["cancelled"]), row["transfer"]) in ("late", "cancelled")


class History:
    def __init__(self, path: Path | str) -> None:
        self.lock = threading.Lock()
        self.db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=2000")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                log.error("%s has schema %d, this code knows %d: history is off", path, version,
                          SCHEMA_VERSION)
                db.close()
                return
            for v in range(version + 1, SCHEMA_VERSION + 1):
                db.execute("BEGIN")
                for statement in MIGRATIONS[v]:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {v}")
                db.execute("COMMIT")
                log.info("%s: schema %d", path, v)
            self.db = db
        except sqlite3.Error as exc:
            log.warning("history is off: %s: %s", path, exc)

    def on_fresh(self, key: str, value, now: datetime) -> None:
        """The collector's hook: each fresh NS answer, whatever the wall picks."""
        if key in ORIGINS:
            self.record_trips(ORIGINS[key], value, now)

    def record_trips(self, origin: str, trips: list[Departure], seen: datetime) -> None:
        """One NS answer: each train's best routing, inserted or updated."""
        if self.db is None:
            return
        best: dict[datetime, Departure] = {}
        for d in filter_dominated(trips):
            if d.planned not in best or _rank(d) < _rank(best[d.planned]):
                best[d.planned] = d
        rows = [(origin, stamp(d.planned), stamp(d.departs), d.delay_min, int(d.cancelled),
                 stamp(d.arrives_planned), stamp(d.arrives), d.transfer.name.lower(), d.track,
                 d.leg_count, stamp(seen), stamp(seen)) for d in best.values()]
        with self.lock:
            try:
                self.db.execute("BEGIN")
                self.db.executemany(
                    """INSERT INTO trains VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (origin, planned) DO UPDATE SET
                         departs = excluded.departs, delay_min = excluded.delay_min,
                         cancelled = excluded.cancelled, arrives_planned = excluded.arrives_planned,
                         arrives = excluded.arrives, transfer = excluded.transfer,
                         track = excluded.track, legs = excluded.legs,
                         last_seen = excluded.last_seen""", rows)
                self.db.execute("COMMIT")
            except sqlite3.Error as exc:
                log.warning("history: could not record %s trains: %s", origin, exc)
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")

    def train_rows(self, since: datetime, until: datetime) -> list[dict] | None:
        """The rows planned in [since, until), oldest first; None when off.
        Compared as text, which is exact except within an hour of a window
        edge that falls across a DST switch."""
        if self.db is None:
            return None
        with self.lock:
            try:
                cur = self.db.execute(
                    "SELECT * FROM trains WHERE planned >= ? AND planned < ? ORDER BY planned, origin",
                    (stamp(since), stamp(until)))
                return [dict(r) for r in cur]
            except sqlite3.Error as exc:
                log.warning("history: could not read trains: %s", exc)
                return None

    def trains_summary(self, now: datetime, days: int = 30, recent_days: int = 7) -> dict | None:
        """What /history/trains serves. `by_departure`: Centraal's weekday
        trains of the last `days` days per planned time, only those seen
        within 10 minutes of leaving. `disrupted`: the last `recent_days`
        days' late, cancelled or transfer-troubled trains, newest first."""
        rows = self.train_rows(now - timedelta(days=days), now)
        if rows is None:
            return None
        rows = [r for r in rows if counts(r)]

        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            planned = datetime.fromisoformat(r["planned"])
            if r["origin"] == "CTR" and planned.weekday() < 5:
                groups[f"{planned:%H:%M}"].append(r)
        by_departure = []
        for hhmm, group in sorted(groups.items()):
            running = [r for r in group if not r["cancelled"]]
            delays = [r["delay_min"] for r in running]
            by_departure.append({
                "time": hhmm,
                "trains": len(group),
                "late": sum(d >= LATE_MIN for d in delays),
                "cancelled": len(group) - len(running),
                "transfer_trouble": sum(r["transfer"] != "ok" for r in running),
                "avg_delay_min": round(sum(delays) / len(delays), 1) if delays else None,
                "max_delay_min": max(delays) if delays else None,
            })

        recent_since = (now - timedelta(days=recent_days)).replace(tzinfo=TZ)
        recent = [{
            "origin": r["origin"], "planned": r["planned"], "departs": r["departs"],
            "delay_min": r["delay_min"], "cancelled": bool(r["cancelled"]), "transfer": r["transfer"],
            "arrives": r["arrives"], "arrival_delay_min": _minutes(r["arrives"], r["arrives_planned"]),
        } for r in reversed(rows)
            if datetime.fromisoformat(r["planned"]) >= recent_since and disrupted(r)]

        return {
            "generated_at": stamp(now),
            "days": days,
            "late_min": LATE_MIN,
            "by_departure": by_departure,
            "recent_days": recent_days,
            "disrupted": recent,
            "recorded": self.recorded(),
        }

    def recorded(self) -> dict | None:
        """How much the table holds: for /status, and to see it grow."""
        if self.db is None:
            return None
        with self.lock:
            try:
                n, first = self.db.execute("SELECT count(*), min(planned) FROM trains").fetchone()
                return {"trains": n, "since": first}
            except sqlite3.Error:
                return None
