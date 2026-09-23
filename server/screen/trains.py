"""The train picker: three departure slots from Den Haag Centraal, with clean
Den Haag HS substitutes when a Centraal slot is disrupted.

Ported from A_Calculations.ino. The policy is specified in CLAUDE.md,
"Train picker policy"; the tests in tests/test_trains.py follow it rule by rule.
"""

from datetime import datetime, timedelta

from .model import Departure
from .weather import parse_local

DISRUPTED_DELAY_MIN = 10
SUBSTITUTE_WINDOW_MIN = 10
WALK_MIN = 5


def calculate_delay(planned: str, actual: str) -> int:
    """Minutes between two ISO timestamps, from the HH:MM fields only.
    Crossing midnight: planned 23:58, actual 00:02 gives +4."""
    if not planned or not actual or len(planned) < 16 or len(actual) < 16:
        return 0
    p = int(planned[11:13]) * 60 + int(planned[14:16])
    a = int(actual[11:13]) * 60 + int(actual[14:16])
    delay = a - p
    if delay < -120:
        delay += 24 * 60
    return delay


def ctr_has_disruption(ctr: list[Departure]) -> bool:
    """True if the picker may need HS data: no Centraal trips at all, or one of
    the first five cancelled or 10+ minutes late."""
    if not ctr:
        return True
    return any(d.cancelled or d.delay_min >= DISRUPTED_DELAY_MIN for d in ctr[:5])


def filter_dominated(trips: list[Departure]) -> list[Departure]:
    """Drop trips that another trip beats: it leaves later and arrives no later.
    Cancelled trips never dominate and are never dropped this way."""
    trips = trips[:12]
    keep = []
    for i, t in enumerate(trips):
        dominated = False
        if not t.cancelled and t.time and t.uni_arr:
            for j, o in enumerate(trips):
                if i == j or o.cancelled or not o.time or not o.uni_arr:
                    continue
                if o.time > t.time and o.uni_arr <= t.uni_arr:
                    dominated = True
                    break
        if not dominated:
            keep.append(t)
    return keep


def pick_departures(ctr: list[Departure], hs: list[Departure], now: datetime) -> list[Departure]:
    ctr, hs = ctr[:12], hs[:12]
    out: list[Departure] = []
    reachable = now + timedelta(minutes=WALK_MIN)

    def arrival_is_useful(cand: Departure) -> bool:
        # Strictly earlier than every filled slot's arrival; unknown arrivals pass.
        if not cand.uni_arr:
            return True
        return all(not s.uni_arr or cand.uni_arr < s.uni_arr for s in out)

    # Trip Planner returned nothing for Centraal: HS becomes primary, same filters.
    if not ctr:
        for t in hs:
            if len(out) == 3:
                break
            if t.cancelled or t.leg_count > 2:
                continue
            when = parse_local(t.planned_iso)
            if when is None or when < reachable:
                continue
            if out and t.planned_iso == out[-1].planned_iso:
                continue
            if not arrival_is_useful(t):
                continue
            out.append(t)
        return out

    ctr_used = [False] * len(ctr)
    hs_used = [False] * len(hs)
    prev_pick: datetime | None = None

    while len(out) < 3:
        idx, ctr_time = -1, None
        for i, t in enumerate(ctr):
            if ctr_used[i]:
                continue
            when = parse_local(t.planned_iso)
            if when is None or (prev_pick is not None and when <= prev_pick):
                continue
            idx, ctr_time = i, when
            break
        if idx < 0:
            break

        # NS returns the same train more than once (different routings).
        for i, t in enumerate(ctr):
            if t.planned_iso == ctr[idx].planned_iso:
                ctr_used[i] = True

        pick = ctr[idx]
        good = not pick.cancelled and pick.delay_min < DISRUPTED_DELAY_MIN
        if not good:
            sub = -1
            for i, t in enumerate(hs):
                if hs_used[i] or t.cancelled or t.leg_count > 2:
                    continue
                when = parse_local(t.planned_iso)
                if when is None:
                    continue
                # C truncates the minute difference toward zero.
                delta_min = int((when - ctr_time).total_seconds() / 60)
                if abs(delta_min) > SUBSTITUTE_WINDOW_MIN:
                    continue
                if when < reachable:
                    continue
                if not arrival_is_useful(t):
                    continue
                sub = i
                break
            if sub >= 0:
                for i, t in enumerate(hs):
                    if t.planned_iso == hs[sub].planned_iso:
                        hs_used[i] = True
                pick = hs[sub]
            # else: show the disrupted Centraal trip; the delay or cancellation is visible.

        out.append(pick)
        prev_pick = ctr_time

    return out
