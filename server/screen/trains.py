"""The train picker: three departure slots from Den Haag Centraal, with clean
Den Haag HS substitutes when a Centraal slot is disrupted.

The policy is specified in CLAUDE.md, "Train picker policy"; the tests in
tests/test_trains.py follow it rule by rule. All comparisons use full
timestamps, so trains arriving after midnight order correctly.
"""

from datetime import datetime, timedelta

from .model import Departure

DISRUPTED_DELAY_MIN = 10          # a Centraal train this late is worth replacing
SUBSTITUTE_WINDOW = timedelta(minutes=10)
WALK_TIME = timedelta(minutes=5)  # an HS substitute must still be reachable on foot
LOOKAHEAD = timedelta(hours=3)    # after the last train of the evening, show nothing rather than tomorrow's


def is_disrupted(d: Departure) -> bool:
    return d.cancelled or d.delay_min >= DISRUPTED_DELAY_MIN


def ctr_has_disruption(ctr: list[Departure]) -> bool:
    """Whether the picker may need fresh HS data: no Centraal trips at all, or
    one of the first five disrupted. Five, not three, because NS lists some
    trains twice."""
    return not ctr or any(is_disrupted(d) for d in ctr[:5])


def filter_dominated(trips: list[Departure]) -> list[Departure]:
    """Drop trips that another trip beats: it leaves later and arrives no later
    (NS lists slow routings next to the direct one). Cancelled trips never
    dominate and are never dropped, so a disruption stays visible."""
    def beats(o: Departure, t: Departure) -> bool:
        return (not o.cancelled and o.arrives is not None
                and o.departs > t.departs and o.arrives <= t.arrives)

    return [t for t in trips
            if t.cancelled or t.arrives is None or not any(beats(o, t) for o in trips if o is not t)]


def pick_departures(ctr: list[Departure], hs: list[Departure], now: datetime) -> list[Departure]:
    horizon = now + LOOKAHEAD
    ctr = sorted((d for d in ctr if d.planned <= horizon), key=lambda d: d.planned)
    hs = sorted((d for d in hs if d.planned <= horizon), key=lambda d: d.planned)
    out: list[Departure] = []

    def is_clean_hs(d: Departure) -> bool:
        # More than two legs means a via-Rotterdam routing with extra changes.
        return not d.cancelled and d.leg_count <= 2 and d.planned >= now + WALK_TIME

    def adds_an_arrival(d: Departure) -> bool:
        # Catching the same Breda sprinter as a slot already shown adds nothing.
        return d.arrives is None or all(s.arrives is None or d.arrives < s.arrives for s in out)

    # No Centraal trips (Trip Planner trouble): HS becomes primary, same filters.
    if not ctr:
        for d in hs:
            if len(out) == 3:
                break
            if is_clean_hs(d) and adds_an_arrival(d) and not any(s.planned == d.planned for s in out):
                out.append(d)
        return out

    used_hs: set[datetime] = set()
    seen_ctr: set[datetime] = set()
    for slot in ctr:
        if len(out) == 3:
            break
        if slot.planned in seen_ctr:        # the same train under another routing
            continue
        seen_ctr.add(slot.planned)

        if not is_disrupted(slot):
            out.append(slot)
            continue
        substitute = next((d for d in hs
                           if d.planned not in used_hs
                           and abs(d.planned - slot.planned) <= SUBSTITUTE_WINDOW
                           and is_clean_hs(d) and adds_an_arrival(d)), None)
        if substitute:
            used_hs.add(substitute.planned)
            out.append(substitute)
        else:
            # Show the disrupted Centraal train, its delay or cancellation visible.
            out.append(slot)
    return out
