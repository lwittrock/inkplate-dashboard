from datetime import date, datetime, timedelta

from screen.schedule import plan, refresh_mode

WED = datetime(2026, 9, 23)   # a Wednesday
SAT = datetime(2026, 9, 26)


def at(day: datetime, hhmmss: str) -> datetime:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return day.replace(hour=h, minute=m, second=s)


def wakes_at(now: datetime, **kw) -> datetime:
    return now + timedelta(seconds=plan(now, failing=False, ota_sent_for=None, **kw).sleep_s)


def test_weekday_peak_is_every_15_minutes_landing_after_a_render():
    assert wakes_at(at(WED, "08:00:40")) == at(WED, "08:15:30")


def test_weekday_off_peak_is_every_30_minutes():
    assert wakes_at(at(WED, "11:00:30")) == at(WED, "11:30:30")


def test_weekend_is_every_15_minutes_all_day():
    assert wakes_at(at(SAT, "11:00:30")) == at(SAT, "11:15:30")


def test_drift_is_corrected_on_every_wake():
    assert wakes_at(at(WED, "11:02:10")) == at(WED, "11:30:30")    # woke late
    assert wakes_at(at(WED, "10:59:50")) == at(WED, "11:30:30")    # woke early


def test_the_last_evening_wake_sleeps_until_the_ota_wake():
    thu = WED + timedelta(days=1)
    assert wakes_at(at(WED, "23:10:30")) == at(thu, "00:05:30")
    p = plan(at(WED, "23:10:30"), failing=False, ota_sent_for=None)
    assert p.draw and not p.ota


def test_the_ota_wake_keeps_the_panel_and_asks_for_one_check():
    now = at(WED + timedelta(days=1), "00:05:40")
    p = plan(now, failing=False, ota_sent_for=None)
    assert (p.draw, p.ota) == (False, True)
    assert now + timedelta(seconds=p.sleep_s) == at(WED + timedelta(days=1), "06:30:30")
    assert not plan(now, failing=False, ota_sent_for=date(2026, 9, 24)).ota


def test_a_failing_device_is_redrawn_even_at_night():
    assert plan(at(WED, "23:45:00"), failing=True, ota_sent_for=None).draw
    assert not plan(at(WED, "23:45:00"), failing=False, ota_sent_for=None).draw


def test_nights_with_a_clock_change_sleep_real_seconds():
    autumn = plan(datetime(2026, 10, 25, 0, 5, 30), failing=False, ota_sent_for=None)
    spring = plan(datetime(2027, 3, 28, 0, 5, 30), failing=False, ota_sent_for=None)
    assert autumn.sleep_s == 6 * 3600 + 25 * 60 + 3600     # the night is an hour longer
    assert spring.sleep_s == 6 * 3600 + 25 * 60 - 3600


def test_every_minute_of_a_week_gives_a_sleep_the_device_accepts():
    t = datetime(2026, 9, 21, 0, 0, 20)
    while t < datetime(2026, 9, 28):
        p = plan(t, failing=False, ota_sent_for=None)
        assert 300 <= p.sleep_s <= 28800, t
        target = t + timedelta(seconds=p.sleep_s)
        assert target.minute % 5 == 0 and target.second == 30, t
        t += timedelta(minutes=1)


def test_refresh_mode():
    assert refresh_mode(wake=8, failing=False, new_firmware=False) == "full"
    assert refresh_mode(wake=9, failing=False, new_firmware=False) == "partial"
    assert refresh_mode(wake=9, failing=True, new_firmware=False) == "full"
    assert refresh_mode(wake=9, failing=False, new_firmware=True) == "full"
    assert refresh_mode(wake=None, failing=False, new_firmware=False) == "full"
