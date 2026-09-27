import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from screen.collect import Collector
from screen.config import Settings
from screen.history import SCHEMA_VERSION, History
from screen.model import Departure, Transfer
from screen.sources import FixtureFetcher

FIXTURE = Path(__file__).parent / "fixtures" / "wall1"
MON = datetime(2026, 9, 28)          # a Monday


def dep(hhmm, day=MON, delay=0, cancelled=False, arrive=None, legs=2, transfer=Transfer.OK,
        origin="CTR"):
    planned = day.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]))
    arrives_planned = planned + timedelta(minutes=81) if arrive is None else arrive
    return Departure(origin=origin, planned=planned, departs=planned + timedelta(minutes=delay),
                     arrives=arrives_planned + timedelta(minutes=delay), track="3",
                     delay_min=0 if cancelled else delay, cancelled=cancelled, transfer=transfer,
                     leg_count=legs, arrives_planned=arrives_planned)


def seen_until_leaving(h, trips, origin="CTR"):
    """As the service sees a train: an answer every 5 minutes until it leaves."""
    for d in trips:
        for m in (20, 15, 10, 5, 1):
            h.record_trips(origin, [d], d.departs - timedelta(minutes=m))


def test_the_schema_is_created_once_and_the_rows_survive(tmp_path):
    path = tmp_path / "history.db"
    History(path).record_trips("CTR", [dep("07:49")], MON.replace(hour=7, minute=40))
    h = History(path)
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert h.recorded() == {"trains": 1, "since": "2026-09-28T07:49:00+02:00"}


def test_a_newer_schema_switches_history_off_and_leaves_the_file_alone(tmp_path):
    path = tmp_path / "history.db"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    h = History(path)
    h.record_trips("CTR", [dep("07:49")], MON)            # does nothing, raises nothing
    assert (h.db, h.recorded(), h.trains_summary(MON)) == (None, None, None)
    assert sqlite3.connect(path).execute("SELECT name FROM sqlite_master").fetchall() == []


def test_an_unusable_path_never_breaks_anything(tmp_path):
    h = History(tmp_path / "missing" / "history.db")
    h.record_trips("CTR", [dep("07:49")], MON)
    assert h.trains_summary(MON) is None


def test_one_row_per_train_its_best_routing_updated_until_it_leaves():
    h = History(":memory:")
    fast, slow = dep("07:49"), dep("07:49", arrive=MON.replace(hour=9, minute=40), legs=3)
    h.record_trips("CTR", [slow, fast], MON.replace(hour=7, minute=30))
    late = dep("07:49", delay=6)
    h.record_trips("CTR", [late], MON.replace(hour=7, minute=45))
    [row] = h.train_rows(MON, MON + timedelta(days=1))
    assert (row["delay_min"], row["departs"], row["arrives_planned"], row["legs"]) == \
        (6, "2026-09-28T07:55:00+02:00", "2026-09-28T09:10:00+02:00", 2)
    assert (row["first_seen"], row["last_seen"]) == ("2026-09-28T07:30:00+02:00", "2026-09-28T07:45:00+02:00")


def test_a_dominated_trip_is_not_a_train_worth_recording():
    h = History(":memory:")
    # 07:44 arrives no earlier than 07:49's train: the picker would never show it.
    h.record_trips("CTR", [dep("07:44", arrive=MON.replace(hour=9, minute=10)), dep("07:49")],
                   MON.replace(hour=7, minute=30))
    assert [r["planned"][11:16] for r in h.train_rows(MON, MON + timedelta(days=1))] == ["07:49"]


def test_the_summary_counts_weekday_centraal_trains_seen_as_they_left():
    h = History(":memory:")
    for week in range(3):
        day = MON + timedelta(days=7 * week)
        seen_until_leaving(h, [dep("07:49", day, delay=[0, 7, 2][week]),
                               dep("08:19", day, cancelled=week == 1),
                               dep("08:49", day, transfer=Transfer.LATE if week == 2 else Transfer.OK)])
    sat = MON + timedelta(days=5)
    seen_until_leaving(h, [dep("07:49", sat, delay=20)])                          # a weekend
    seen_until_leaving(h, [dep("07:54", MON, delay=9, origin="HS")], origin="HS")  # HS
    # Seen only half an hour before it left: its last word is not the one that counts.
    h.record_trips("CTR", [dep("09:19", MON)], MON.replace(hour=8, minute=49))

    s = h.trains_summary(MON + timedelta(days=15, hours=12))
    assert s["by_departure"] == [
        {"time": "07:49", "trains": 3, "late": 1, "cancelled": 0, "transfer_trouble": 0,
         "avg_delay_min": 3.0, "max_delay_min": 7},
        {"time": "08:19", "trains": 3, "late": 0, "cancelled": 1, "transfer_trouble": 0,
         "avg_delay_min": 0.0, "max_delay_min": 0},
        {"time": "08:49", "trains": 3, "late": 0, "cancelled": 0, "transfer_trouble": 1,
         "avg_delay_min": 0.0, "max_delay_min": 0},
    ]
    # The last week's disruptions, newest first, HS and weekends included.
    assert [(r["origin"], r["planned"][:16]) for r in s["disrupted"]] == [
        ("CTR", "2026-10-12T08:49")]
    s = h.trains_summary(MON + timedelta(days=6))
    assert [(r["origin"], r["planned"][:16], r["delay_min"], r["arrival_delay_min"]) for r in s["disrupted"]] == [
        ("CTR", "2026-10-03T07:49", 20, 20), ("HS", "2026-09-28T07:54", 9, 9)]
    assert s["recorded"]["trains"] == 12


def test_a_train_not_yet_gone_is_left_out_until_it_is():
    h = History(":memory:")
    h.record_trips("CTR", [dep("07:49", delay=15)], MON.replace(hour=7, minute=50))
    assert h.trains_summary(MON.replace(hour=7, minute=55))["by_departure"] == []


def test_fresh_answers_reach_the_hook_and_cached_ones_do_not():
    fresh = []
    c = Collector(Settings.from_env(), FixtureFetcher(FIXTURE), on_fresh=lambda k, v, t: fresh.append(k))
    now = datetime(2026, 9, 23, 21, 39, 30)
    c.snapshot(now)
    c.snapshot(now + timedelta(minutes=1))
    assert sorted(fresh) == ["br_feed", "br_rain", "ns_ctr", "ns_hs", "om"]


def test_a_failing_hook_costs_nothing():
    def boom(*_):
        raise RuntimeError("history broke")
    c = Collector(Settings.from_env(), FixtureFetcher(FIXTURE), on_fresh=boom)
    snap = c.snapshot(datetime(2026, 9, 23, 21, 39, 30))
    assert len(snap.departures) == 2 and snap.weather and snap.forecast
