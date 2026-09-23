"""The train picker, rule by rule, from CLAUDE.md "Train picker policy"."""

from datetime import datetime, timedelta

from screen.model import Departure, Transfer
from screen.trains import ctr_has_disruption, filter_dominated, pick_departures

NOW = datetime(2026, 9, 23, 8, 0)


def at(hhmm: str) -> datetime:
    """"08:10" today; "+00:10" tomorrow."""
    day = NOW + timedelta(days=1) if hhmm.startswith("+") else NOW
    h, m = hhmm.lstrip("+").split(":")
    return day.replace(hour=int(h), minute=int(m))


def dep(time, arr, origin="CTR", delay=0, cancelled=False, legs=2, planned=None):
    return Departure(
        origin=origin, planned=at(planned or time), departs=at(time),
        arrives=at(arr) if arr else None, track="5", delay_min=delay, cancelled=cancelled,
        transfer=Transfer.OK, leg_count=legs)


def times(picked):
    return [(d.origin, d.time) for d in picked]


def test_good_centraal_trips_are_used_as_is():
    ctr = [dep("08:10", "09:20"), dep("08:40", "09:50"), dep("09:10", "10:20")]
    assert times(pick_departures(ctr, [], NOW)) == [("CTR", "08:10"), ("CTR", "08:40"), ("CTR", "09:10")]


def test_nine_minutes_late_is_still_good_ten_is_not():
    hs = [dep("08:12", "09:10", "HS")]
    ctr = [dep("08:19", "09:29", delay=9, planned="08:10")]
    assert pick_departures(ctr, hs, NOW)[0].origin == "CTR"
    ctr = [dep("08:20", "09:30", delay=10, planned="08:10")]
    assert pick_departures(ctr, hs, NOW)[0].origin == "HS"


def test_cancelled_centraal_gets_a_clean_hs_substitute():
    ctr = [dep("08:10", "09:20", cancelled=True), dep("08:40", "09:50")]
    hs = [dep("08:14", "09:20", "HS")]
    assert times(pick_departures(ctr, hs, NOW)) == [("HS", "08:14"), ("CTR", "08:40")]


def test_substitute_with_more_than_two_legs_is_rejected():
    ctr = [dep("08:10", "09:20", cancelled=True)]
    hs = [dep("08:12", "09:25", "HS", legs=3)]
    picked = pick_departures(ctr, hs, NOW)
    assert times(picked) == [("CTR", "08:10")]
    assert picked[0].cancelled  # the disruption stays visible


def test_substitute_must_leave_within_ten_minutes_of_the_centraal_slot():
    ctr = [dep("08:30", "09:40", cancelled=True)]
    assert pick_departures(ctr, [dep("08:19", "09:30", "HS")], NOW)[0].origin == "CTR"   # 11 min early
    assert pick_departures(ctr, [dep("08:20", "09:30", "HS")], NOW)[0].origin == "HS"    # 10 min early
    assert pick_departures(ctr, [dep("08:40", "09:39", "HS")], NOW)[0].origin == "HS"    # 10 min late
    assert pick_departures(ctr, [dep("08:41", "09:39", "HS")], NOW)[0].origin == "CTR"   # 11 min late


def test_substitute_must_be_reachable_on_foot():
    ctr = [dep("08:06", "09:20", cancelled=True)]
    assert pick_departures(ctr, [dep("08:04", "09:15", "HS")], NOW)[0].origin == "CTR"   # now + 4
    assert pick_departures(ctr, [dep("08:05", "09:15", "HS")], NOW)[0].origin == "HS"    # now + 5


def test_substitute_must_arrive_before_every_filled_slot():
    # Slot 1 arrives 09:20. The HS option for the cancelled slot 2 catches the
    # same connection, so it adds nothing and the disruption is shown instead.
    ctr = [dep("08:10", "09:20"), dep("08:40", "09:50", cancelled=True)]
    hs = [dep("08:36", "09:20", "HS")]
    assert times(pick_departures(ctr, hs, NOW)) == [("CTR", "08:10"), ("CTR", "08:40")]


def test_duplicate_routings_of_one_train_fill_one_slot():
    ctr = [dep("08:10", "09:20"), dep("08:10", "09:22"), dep("08:40", "09:50")]
    assert times(pick_departures(ctr, [], NOW)) == [("CTR", "08:10"), ("CTR", "08:40")]


def test_one_hs_train_substitutes_only_once():
    ctr = [dep("08:10", "09:30", cancelled=True), dep("08:15", "09:35", cancelled=True)]
    hs = [dep("08:12", "09:20", "HS")]
    assert times(pick_departures(ctr, hs, NOW)) == [("HS", "08:12"), ("CTR", "08:15")]


def test_no_centraal_trips_promotes_clean_hs():
    hs = [
        dep("08:03", "09:05", "HS"),             # unreachable
        dep("08:10", "09:15", "HS", legs=3),     # via-Rotterdam ghost
        dep("08:12", "09:20", "HS", cancelled=True),
        dep("08:14", "09:20", "HS"),
        dep("08:14", "09:21", "HS"),             # same train again
        dep("08:30", "09:20", "HS"),             # arrives no earlier than slot 1
        dep("08:44", "09:10", "HS"),
        dep("09:14", "10:05", "HS"),
    ]
    assert times(pick_departures([], hs, NOW)) == [("HS", "08:14"), ("HS", "08:44")]


def test_evening_trains_arriving_after_midnight():
    # 23 September 2026, 21:33: the firmware compared "HH:MM" text, so the
    # 22:49 arriving "00:10" beat everything and one card was left.
    now = NOW.replace(hour=21, minute=33)
    ctr = [dep("21:49", "23:10"), dep("22:49", "+00:10"),
           dep("+04:44", "+06:40", legs=3), dep("+05:49", "+07:10")]
    kept = filter_dominated(ctr)
    assert len(kept) == 4
    # Tomorrow's trains are beyond the three-hour look-ahead.
    assert times(pick_departures(kept, [], now)) == [("CTR", "21:49"), ("CTR", "22:49")]


def test_filter_dominated_drops_trips_beaten_by_a_later_departure():
    trips = [dep("08:10", "09:30"), dep("08:15", "09:30"), dep("08:20", "09:25"), dep("08:25", "09:40")]
    assert [t.time for t in filter_dominated(trips)] == ["08:20", "08:25"]


def test_cancelled_trips_neither_dominate_nor_get_dropped():
    trips = [dep("08:10", "09:30", cancelled=True), dep("08:15", "09:30"), dep("08:20", "09:20", cancelled=True)]
    assert [t.time for t in filter_dominated(trips)] == ["08:10", "08:15", "08:20"]


def test_disruption_check_looks_at_the_first_five_trips():
    clean = [dep(f"08:{m:02d}", f"09:{m:02d}") for m in range(0, 50, 10)]
    assert not ctr_has_disruption(clean)
    assert ctr_has_disruption([])
    assert ctr_has_disruption(clean[:4] + [dep("08:55", "09:55", delay=10)])
    assert not ctr_has_disruption(clean + [dep("08:55", "09:55", cancelled=True)])
