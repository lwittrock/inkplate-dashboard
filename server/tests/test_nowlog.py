import json
from datetime import datetime, timedelta

from screen.nowlog import KEEP, NowLog


def rec(t: datetime) -> dict:
    return {"t": t.isoformat(timespec="seconds"), "shown": "clear"}


def times(body: bytes) -> list[str]:
    return [json.loads(line)["t"] for line in body.decode().splitlines()]


def test_records_come_back_from_a_time_on(tmp_path):
    log = NowLog(tmp_path / "now.jsonl")
    t0 = datetime(2026, 9, 25, 9, 0)
    for m in (0, 5, 10):
        log.append(rec(t0 + timedelta(minutes=m)), t0)
    assert times(log.since(t0 + timedelta(minutes=5))) == ["2026-09-25T09:05:00", "2026-09-25T09:10:00"]
    assert log.since(t0 + timedelta(days=1)) == b""


def test_old_records_are_dropped_once_a_day(tmp_path):
    log = NowLog(tmp_path / "now.jsonl")
    old, today = datetime(2026, 9, 1, 12, 0), datetime(2026, 9, 25, 9, 0)
    log.append(rec(old), old)
    log.append(rec(today - KEEP + timedelta(hours=1)), today)      # trims: day changed
    log.append(rec(today), today)
    assert times(log.since(datetime(2026, 1, 1))) == ["2026-09-11T10:00:00", "2026-09-25T09:00:00"]


def test_a_failed_write_never_breaks_the_render(tmp_path):
    log = NowLog(tmp_path / "missing" / "now.jsonl")
    log.append(rec(datetime(2026, 9, 25, 9, 0)), datetime(2026, 9, 25, 9, 0))
    assert log.since(datetime(2026, 1, 1)) == b""
