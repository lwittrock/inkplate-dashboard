"""The dashboard layout, drawn onto an 800x600 1-bit canvas.

A section-by-section port of C_Display.ino. All Y coordinates are absolute
and the band comments are the layout contract, as they were in the firmware:
moving one band without its neighbours silently overlaps content.

    y=0    masthead: greeting, date, sun/moon arc
    y=92   thick rule
    y=112  WEATHER | RAIN COMING / NEXT HOURS DRY
    y=305  dotted divider
    y=324  WEEK: 7 cells x 102 px
    y=455  dotted divider
    y=474  TRAINS -> BREDA: 3 cards x 220 px
    y=590  footer: updated HH:MM, battery
"""

import math
import struct

from .gfx import BLACK, WHITE, Canvas
from .headline import greeting
from .model import (Category, Departure, DayForecast, RainSample, Snapshot, Transfer,
                    round_half_away)
from .telemetry import battery_percent

MARGIN_LEFT = 40
MARGIN_RIGHT = 760
FOOTER_Y = 590

REG9, REG12 = "Inter_Regular9pt7b", "Inter_Regular12pt7b"
BOLD9, BOLD12, BOLD18, BOLD48 = "Inter_Bold9pt7b", "Inter_Bold12pt7b", "Inter_Bold18pt7b", "Inter_Bold48pt7b"

DAYS_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def f32(x: float) -> float:
    """Round to single precision, where the firmware's float arithmetic
    decides a pixel (the small-caps gaps, the sun arc's sample steps)."""
    return struct.unpack("f", struct.pack("f", x))[0]


def hhmm(s: str) -> int:
    """"HH:MM" -> minutes since midnight, -1 on bad input."""
    if not s or len(s) < 5:
        return -1
    d = [ord(ch) - 48 for ch in s[:5]]
    return d[0] * 600 + d[1] * 60 + d[3] * 10 + d[4]


# --- primitives ---------------------------------------------------------------

def dashed_h(c: Canvas, x1: int, x2: int, y: int, dash: int = 2, gap: int = 3) -> None:
    x = x1
    while x <= x2:
        end = min(x + dash - 1, x2)
        c.hline(x, y, end - x + 1)
        x = end + 1 + gap


def dashed_v(c: Canvas, x: int, y1: int, y2: int, dash: int = 2, gap: int = 3) -> None:
    y = y1
    while y <= y2:
        end = min(y + dash - 1, y2)
        c.vline(x, y, end - y + 1)
        y = end + 1 + gap


def fill_bayer50(c: Canvas, x: int, y: int, w: int, h: int) -> None:
    """Single-pixel checker; the parity is global so neighbouring fills line up."""
    for j in range(h):
        for i in range(((y + j) & 1) ^ (x & 1), w, 2):
            c.pixel(x + i, y + j)


def right_arrow(c: Canvas, x: int, y: int) -> None:
    """7x3 px arrow centred on y; U+2192 is not in the fonts."""
    c.hline(x, y, 6)
    for px, py in ((x + 4, y - 1), (x + 5, y - 1), (x + 4, y + 1), (x + 5, y + 1)):
        c.pixel(px, py)


def degree_ring(c: Canvas, cx: int, cy: int, outer: int, inner: int) -> None:
    c.fill_circle(cx, cy, outer, BLACK)
    c.fill_circle(cx, cy, inner, WHITE)


_F022 = f32(0.22)


def _caps_gap(adv: int, extra: int) -> int:
    return int(f32(f32(_F022 * adv) + 0.5)) + extra


def small_caps(c: Canvas, x: int, y: int, text: str, color: int = BLACK, extra: int = 1) -> None:
    """Uppercase with letter-spacing proportional to each glyph's advance
    (the mockup's letter-spacing: 0.22em). Uses the current font."""
    c.text_color = color
    c.set_cursor(x, y)
    prev_x = x
    for ch in text.upper():
        if ch == " ":
            nx = c.cursor_x + 5 + extra
            c.set_cursor(nx, y)
            prev_x = nx
            continue
        c.write(ch)
        after = c.cursor_x
        nx = after + _caps_gap(after - prev_x, extra)
        c.set_cursor(nx, y)
        prev_x = nx


def _char_advance(c: Canvas, ch: str) -> int:
    # bbox("XX") - bbox("X") is the advance of X; the bbox alone is not.
    w1 = c.text_width(ch)
    adv = c.text_width(ch + ch) - w1
    return adv if adv >= 1 else w1


def small_caps_width(c: Canvas, text: str, extra: int = 1) -> int:
    total = last = 0
    for ch in text.upper():
        if ch == " ":
            total += 5 + extra
            last = extra
            continue
        adv = _char_advance(c, ch)
        gap = _caps_gap(adv, extra)
        total += adv + gap
        last = gap
    return total - last


def center_print(c: Canvas, cx: int, y: int, s: str) -> None:
    if not s:
        return
    c.set_cursor(cx - c.text_width(s) // 2, y)
    c.write(s)


# --- icons ----------------------------------------------------------------------

def icon128(cat: Category, night: bool) -> str:
    if cat == Category.CLEAR:
        return "moon_128" if night else "sun_128"
    if cat == Category.PARTLY_CLOUDY:
        return "cloud_moon_128" if night else "cloud_sun_128"
    return {
        Category.OVERCAST: "cloud_128", Category.FOG: "fog_128",
        Category.DRIZZLE: "rain_light_128", Category.RAIN: "rain_128",
        Category.RAIN_HEAVY: "rain_heavy_128", Category.SNOW: "snow_128",
        Category.THUNDERSTORM: "lightning_128",
    }.get(cat, "cloud_128")


def icon48(cat: Category, sunny: bool) -> str:
    if cat in (Category.DRIZZLE, Category.RAIN, Category.RAIN_HEAVY) and sunny:
        return "sun_rain_48"
    if cat == Category.SNOW and sunny:
        return "sun_snow_48"
    return {
        Category.CLEAR: "sun_48", Category.PARTLY_CLOUDY: "cloud_sun_48",
        Category.OVERCAST: "cloud_48", Category.FOG: "fog_48",
        Category.DRIZZLE: "rain_light_48", Category.RAIN: "rain_48",
        Category.RAIN_HEAVY: "rain_heavy_48", Category.SNOW: "snow_48",
        Category.THUNDERSTORM: "lightning_48",
    }.get(cat, "cloud_48")


# --- masthead (y=0-92) ------------------------------------------------------------

def sun_arc(c: Canvas, x_left: int, x_right: int, base_y: int, rise: int) -> None:
    cx = (x_left + x_right) // 2
    rx = (x_right - x_left) // 2
    if rx <= 0 or rise <= 0:
        return
    t, step, end = 0.0, f32(0.005), f32(1.0001)
    while t <= end:
        angle = math.pi * t
        c.pixel(cx - int(rx * math.cos(angle) + 0.5), base_y - int(rise * math.sin(angle) + 0.5))
        t = f32(t + step)


def sun_dot(x_left: int, x_right: int, base_y: int, rise: int, t: float) -> tuple[int, int]:
    """Point on the arc at t in [0, 1]. X is linear in time so the dot moves
    evenly; Y follows the half-circle so it stays on the drawn arc."""
    t = min(max(t, 0.0), 1.0)
    u = 2.0 * t - 1.0
    x = x_left + int(t * (x_right - x_left) + 0.5)
    y = base_y - int(rise * math.sqrt(1.0 - u * u) + 0.5)
    return x, y


def draw_header(c: Canvas, snap: Snapshot, night: bool) -> None:
    now, fc = snap.now, snap.forecast

    c.set_font(BOLD18)
    c.text_color = BLACK
    c.set_cursor(40, 48)
    current = snap.weather.category if snap.weather else None
    c.write(greeting(now, fc[0] if fc else None, current))

    # "Wed · 23 Sep 2026"; the middot is a small circle.
    day = DAYS_ABBR[now.weekday()]
    c.set_font(REG9)
    c.set_cursor(40, 72)
    c.write(day)
    after_day = 40 + c.text_width(day)
    c.fill_circle(after_day + 6, 68, 2)
    c.set_cursor(after_day + 12, 72)
    c.write(f"{now.day:02d} {MONTHS[now.month - 1]} {now.year}")

    # Right: sunrise-to-sunset arc with a sun by day; sunset-to-sunrise with
    # a crescent at night.
    arc_left, arc_right, horizon_y, arc_rise = 620, 760, 58, 24
    left_time = right_time = ""
    t = -1.0
    if fc:
        now_min = now.hour * 60 + now.minute
        if not night:
            sr, ss = hhmm(fc[0].sunrise), hhmm(fc[0].sunset)
            if sr >= 0 and ss > sr:
                t = (now_min - sr) / (ss - sr)
                left_time, right_time = fc[0].sunrise, fc[0].sunset
        else:
            ss = hhmm(fc[0].sunset)
            sr_tom = hhmm(fc[1].sunrise) if len(fc) >= 2 else -1
            if sr_tom < 0:
                sr_tom = hhmm(fc[0].sunrise)
            if ss >= 0 and sr_tom >= 0:
                night_len = (24 * 60 - ss) + sr_tom
                elapsed = now_min - ss if now_min >= ss else (24 * 60 - ss) + now_min
                if night_len > 0:
                    t = elapsed / night_len
                left_time = fc[0].sunset
                right_time = fc[1].sunrise if len(fc) >= 2 and fc[1].sunrise else fc[0].sunrise

    if t >= 0.0:
        t = min(t, 1.0)
        dashed_h(c, arc_left, arc_right, horizon_y)
        sun_arc(c, arc_left, arc_right, horizon_y, arc_rise)
        dx, dy = sun_dot(arc_left, arc_right, horizon_y, arc_rise, t)
        if night:
            c.fill_circle(dx, dy, 5, BLACK)
            c.fill_circle(dx + 2, dy, 4, WHITE)
        else:
            c.fill_circle(dx, dy, 2)
            for i in range(8):
                a = i * math.pi / 4
                ca, sa = math.cos(a), math.sin(a)
                c.line(dx + int(4 * ca + 0.5), dy + int(4 * sa + 0.5),
                       dx + int(6 * ca + 0.5), dy + int(6 * sa + 0.5))
        c.set_font(REG9)
        center_print(c, arc_left, 78, left_time)
        center_print(c, arc_right, 78, right_time)
    elif night:
        c.fill_circle(690, 50, 14, BLACK)
        c.fill_circle(684, 50, 12, WHITE)

    c.fill_rect(40, 92, MARGIN_RIGHT - 40 + 1, 2)


# --- weather row, left (y=112-305) ------------------------------------------------

def draw_current_weather(c: Canvas, snap: Snapshot, night: bool) -> None:
    w = snap.weather
    c.draw_icon(40, 125, icon128(w.category, night))

    temp = str(round_half_away(w.temp))
    c.set_font(BOLD48)
    c.text_color = BLACK
    c.set_cursor(195, 210)
    c.write(temp)
    bx, by, bw, _ = c.text_bounds(temp, 195, 210)
    degree_ring(c, bx + bw + 14, by + 8, 9, 4)

    # Wind arrow, centred at (207, 244), 22 px long, pointing downwind.
    cx, cy, r = 207, 244, 11
    a = (90.0 - (w.wind_bearing + 180.0)) * math.pi / 180.0
    ca, sa = math.cos(a), math.sin(a)
    tip_x, tip_y = int(cx + r * ca + 0.5), int(cy - r * sa + 0.5)
    tail_x, tail_y = int(cx - r * ca + 0.5), int(cy + r * sa + 0.5)
    c.line(tail_x, tail_y, tip_x, tip_y)
    back_x, back_y = tip_x - 5 * ca, tip_y + 5 * sa
    px, py = -sa, -ca
    c.fill_triangle(tip_x, tip_y,
                    int(back_x + 3 * px + 0.5), int(back_y + 3 * py + 0.5),
                    int(back_x - 3 * px + 0.5), int(back_y - 3 * py + 0.5))

    c.set_font(REG9)
    c.set_cursor(227, 249)
    c.write(f"{int(w.wind_kmh + 0.5)} km/h")


# --- weather row, right (y=112-276) ----------------------------------------------

def draw_temp_curve(c: Canvas, snap: Snapshot) -> None:
    """24 h temperature curve with sunrise/sunset guides and min/max labels."""
    hourly, fc, now = snap.hourly, snap.forecast, snap.now
    n = len(hourly)
    if n < 2:
        return
    x0, x1, y_top, y_bot = 420, 750, 140, 260
    chart_w, chart_h = x1 - x0, y_bot - y_top

    c.line(x0, y_top, x0, y_bot)
    c.line(x0, y_bot, x1, y_bot)

    t_min = t_max = hourly[0]
    min_idx = max_idx = 0
    for i in range(1, n):
        if hourly[i] < t_min:
            t_min, min_idx = hourly[i], i
        if hourly[i] > t_max:
            t_max, max_idx = hourly[i], i
    t_lo, t_hi = t_min - 1.0, t_max + 1.0
    t_range = t_hi - t_lo
    if t_range < 0.1:
        t_range = 1.0

    def x_for(i: float) -> int:
        return x0 + int(i * chart_w / (n - 1) + 0.5)

    def y_for(t: float) -> int:
        return y_bot - int((t - t_lo) / t_range * chart_h + 0.5)

    if fc:
        now_min = now.hour * 60 + now.minute
        ss_today, sr_today = hhmm(fc[0].sunset), hhmm(fc[0].sunrise)
        sr_tom = hhmm(fc[1].sunrise) if len(fc) >= 2 else sr_today

        def guide(off_h: float, label: str) -> None:
            if off_h < 0.5 or off_h > (n - 1) - 0.5:
                return
            gx = x_for(off_h)
            dashed_v(c, gx, y_top - 4, y_bot - 1)
            c.set_font(REG9)
            c.set_cursor(gx - c.text_width(label) // 2, 132)
            c.write(label)

        def hours_until(event: int) -> float:
            return (event - now_min) / 60 if event > now_min else (event + 24 * 60 - now_min) / 60

        if ss_today >= 0:
            guide(hours_until(ss_today), "sunset")
        sr = sr_today if sr_today > now_min else sr_tom
        if sr >= 0:
            guide(hours_until(sr), "sunrise")

    # Y ticks every 5 degrees, skipped where they would crowd a min/max label.
    c.set_font(REG9)
    y_min_mark, y_max_mark = y_for(hourly[min_idx]), y_for(hourly[max_idx])
    for v in range(math.ceil(t_lo / 5) * 5, math.floor(t_hi / 5) * 5 + 1, 5):
        gy = y_for(v)
        if abs(gy - y_min_mark) < 10 or abs(gy - y_max_mark) < 10:
            continue
        c.hline(x0 - 2, gy, 2)
        label = str(v)
        _, _, bw, bh = c.text_bounds(label)
        c.set_cursor(x0 - 5 - bw, gy + bh // 2)
        c.write(label)

    for i in range(1, n):
        c.line(x_for(i - 1), y_for(hourly[i - 1]), x_for(i), y_for(hourly[i]))

    # Both labels above their dots: below the min would hit the x-axis labels.
    for idx in (max_idx, min_idx):
        px, py = x_for(idx), y_for(hourly[idx])
        c.fill_circle(px, py, 3)
        c.set_font(BOLD9)
        c.text_color = BLACK
        v = hourly[idx]
        label = str(round_half_away(v))
        _, _, bw, bh = c.text_bounds(label)
        text_left, text_y = px - bw // 2 - 3, py - 8
        c.set_cursor(text_left, text_y)
        c.write(label)
        degree_ring(c, text_left + bw + 4, text_y - bh + 3, 2, 1)

    c.set_font(REG9)
    for label, hour in (("now", 0), ("+6h", 6), ("+12h", 12), ("+18h", 18)):
        if hour >= n:
            continue
        center_print(c, x_for(hour), 276, label)


def draw_rain_chart(c: Canvas, rain: list[RainSample]) -> None:
    """Buienradar's 2 h nowcast: up to 24 five-minute samples in mm/h."""
    x0, x1, y_top, y_bot = 420, 750, 140, 260
    chart_h = y_bot - y_top
    heavy, medium, light = 10.0, 4.0, 0.4
    max_scale = 12.0

    values = [s.mmh for s in rain]
    has_rain = any(v > 0.2 for v in values)
    max_rain = max([0.0] + values)
    if max_rain > max_scale:
        max_scale = max_rain + 1.0

    c.line(x0, y_top, x0, y_bot)
    c.line(x0, y_bot, x1, y_bot)

    if not has_rain:
        c.set_font(REG9)
        msg = "No rain expected"
        c.set_cursor(x0 + ((x1 - x0) - c.text_width(msg)) // 2, y_top + chart_h // 2)
        c.write(msg)
        return

    n = min(24, len(rain))
    spacing = (x1 - x0) // (n - 1) if n > 1 else 30

    def x_for(i: int) -> int:
        return x0 + i * spacing

    def y_for(mm: float) -> int:
        h = int((mm / max_scale) * chart_h + 0.5)
        return y_bot - min(max(h, 0), chart_h)

    # Checker fill under the interpolated curve, column by column.
    for i in range(1, n):
        px, py = x_for(i - 1), y_for(values[i - 1])
        cx, cy = x_for(i), y_for(values[i])
        for fx in range(px, cx + 1):
            t = (fx - px) / (cx - px) if cx > px else 0.0
            fy = py + int(t * (cy - py) + 0.5)
            h = (y_bot - 1) - fy
            if h > 0:
                fill_bayer50(c, fx, fy, 1, h)

    for i in range(1, n):
        c.line(x_for(i - 1), y_for(values[i - 1]), x_for(i), y_for(values[i]))

    # Threshold lines, labelled inside the chart on a white plate.
    for mm, label in ((heavy, "Heavy"), (medium, "Medium"), (light, "Light")):
        y = y_for(mm)
        if y < y_top or y > y_bot - 1:
            continue
        dashed_h(c, x0 + 4, x1 - 2, y)
        c.set_font(REG9)
        _, _, bw, bh = c.text_bounds(label)
        plate_w = bw + 6
        c.fill_rect((x1 - 2) - plate_w, y - bh // 2 - 2, plate_w + 1, bh + 4, WHITE)
        c.text_color = BLACK
        c.set_cursor((x1 - 2) - bw - 2, y + bh // 2 - 1)
        c.write(label)

    # Hour ticks where Buienradar's own label is on the hour.
    c.set_font(REG9)
    for i, sample in enumerate(rain[:n]):
        if not sample.label.endswith(":00"):
            continue
        x = x_for(i)
        c.line(x, y_bot, x, y_bot + 4)
        center_print(c, x, 276, sample.label)


# --- week strip (y=324-445) -----------------------------------------------------------

def draw_week(c: Canvas, fc: list[DayForecast]) -> None:
    cell_w, x_start = 102, 40
    ts_lo, ts_hi = -2.0, 25.0       # shared scale for every day's range bar

    for i, day in enumerate(fc[:7]):
        mid = x_start + i * cell_w + cell_w // 2

        c.set_font(BOLD9)
        c.text_color = BLACK
        center_print(c, mid, 340, day.day_name)

        c.draw_icon(mid - 24, 350, icon48(day.category, day.sunny_variant))

        bar_y, bar_l, bar_r = 418, mid - 40, mid + 40
        dashed_h(c, bar_l, bar_r, bar_y)

        def x_for(t: int) -> int:
            clipped = min(max(float(t), ts_lo), ts_hi)
            return bar_l + int((clipped - ts_lo) / (ts_hi - ts_lo) * (bar_r - bar_l) + 0.5)

        x_min, x_max = sorted((x_for(day.temp_min), x_for(day.temp_max)))
        c.hline(x_min, bar_y, x_max - x_min + 1)
        c.hline(x_min, bar_y - 1, x_max - x_min + 1)
        c.fill_circle(x_min, bar_y, 3)
        c.fill_circle(x_max, bar_y, 3)

        c.set_font(BOLD9)
        center_print(c, x_max, 410, str(day.temp_max))
        c.set_font(REG9)
        center_print(c, x_min, 436, str(day.temp_min))


# --- departures (y=474-565) -----------------------------------------------------------

def draw_trains(c: Canvas, deps: list[Departure]) -> None:
    """Three 220x80 cards. A Den Haag HS substitute gets a 2 px outline and a
    black "DH HS" pill; that is the whole origin signal, no note text."""
    if not deps:
        c.set_font(REG12)
        c.set_cursor(40, 510)
        c.write("Train data unavailable")
        return

    card_w, card_h, card_y = 220, 80, 485
    for i, d in enumerate(deps[:3]):
        cx, cy = 40 + i * 240, card_y

        c.rect(cx, cy, card_w, card_h)
        if d.origin == "HS":
            c.rect(cx + 1, cy + 1, card_w - 2, card_h - 2)

        if d.origin == "CTR":
            c.set_font(REG9)
            c.text_color = BLACK
            c.set_cursor(cx + 14, cy + 18)
            c.write("DH Centraal")
        else:
            c.fill_rect(cx + 4, cy + 4, 64, 18)
            c.set_font(REG9)
            text_w = small_caps_width(c, "DH HS", 0)
            small_caps(c, cx + 4 + 32 - text_w // 2, cy + 18, "DH HS", WHITE, 0)

        c.set_font(BOLD12)
        c.text_color = BLACK
        c.set_cursor(cx + 14, cy + 42)
        c.write(d.time)

        if d.cancelled:
            status = "cancelled"
        elif d.delay_min:
            status = f"+{d.delay_min}m late"
        else:
            status = "on time"
        c.set_font(REG9)
        c.set_cursor(cx + 90, cy + 42)
        c.write(status)

        c.set_font(BOLD9)
        c.set_cursor(cx + card_w - 14 - c.text_width(d.track), cy + 42)
        c.write(d.track)

        # No arrival line for a train that isn't running.
        if d.cancelled:
            continue

        tl_x, tl_y = cx + 14, cy + 68
        right_arrow(c, tl_x, tl_y - 4)
        text_x = tl_x + 10
        if d.transfer == Transfer.CANCELLED:
            c.set_font(BOLD9)
            c.set_cursor(text_x, tl_y)
            c.write("Uni ")
            x_now = c.cursor_x
            c.fill_circle(x_now + 3, tl_y - 4, 1)
            c.set_cursor(x_now + 9, tl_y)
            c.write("transfer cancelled")
        else:
            c.set_font(REG9)
            c.set_cursor(text_x, tl_y)
            c.write("Uni " + d.uni_arr)
            if d.transfer == Transfer.LATE:
                x_now = c.cursor_x
                c.fill_circle(x_now + 4, tl_y - 4, 1)
                c.set_font(BOLD9)
                c.set_cursor(x_now + 10, tl_y)
                c.write("transfer late")


# --- footer (y=577-590) -------------------------------------------------------------------

def draw_footer(c: Canvas, snap: Snapshot) -> None:
    c.set_font(REG9)
    c.set_cursor(MARGIN_LEFT, FOOTER_Y)
    c.write(f"Updated {snap.now.hour:02d}:{snap.now.minute:02d}")

    pct = battery_percent(snap.battery_v)     # same LiPo curve as Home Assistant gets

    body_w, body_h, nub_w, nub_h = 28, 14, 3, 6
    icon_top = FOOTER_Y - 13
    body_x = MARGIN_RIGHT - nub_w - body_w
    c.rect(body_x, icon_top, body_w, body_h)
    c.fill_rect(MARGIN_RIGHT - nub_w, icon_top + (body_h - nub_h) // 2, nub_w, nub_h)
    if pct:
        c.fill_rect(body_x + 2, icon_top + 2, int(pct / 100.0 * (body_w - 4)), body_h - 4)

    if snap.firmware:
        c.set_font(REG9)
        c.set_cursor(body_x - 12 - c.text_width(snap.firmware), FOOTER_Y)
        c.write(snap.firmware)


# --- the whole screen ------------------------------------------------------------------------

def is_night(snap: Snapshot) -> bool:
    """Before today's sunrise or after its sunset; 20:00-06:00 without a forecast."""
    now_min = snap.now.hour * 60 + snap.now.minute
    fc = snap.forecast
    sr = hhmm(fc[0].sunrise) if fc else -1
    ss = hhmm(fc[0].sunset) if fc else -1
    if sr >= 0 and ss > sr:
        return now_min < sr or now_min >= ss
    return snap.now.hour >= 20 or snap.now.hour < 6


def render(snap: Snapshot) -> Canvas:
    c = Canvas()
    night = is_night(snap)

    draw_header(c, snap, night)

    c.set_font(REG9)
    small_caps(c, 40, 112, "WEATHER")
    if snap.weather:
        draw_current_weather(c, snap, night)
    else:
        c.set_font(REG12)
        c.set_cursor(MARGIN_LEFT, 200)
        c.write("Weather data unavailable")

    # Right panel: the rain chart when 0.4 mm/h (Buienradar's "light rain"
    # floor) or more is coming in the next 2 h, else the 24 h temperature curve.
    if any(s.mmh >= 0.4 for s in snap.rain):
        c.set_font(REG9)
        small_caps(c, 420, 112, "RAIN COMING")
        draw_rain_chart(c, snap.rain)
    elif len(snap.hourly) >= 2:
        c.set_font(REG9)
        small_caps(c, 420, 112, "NEXT HOURS DRY")
        draw_temp_curve(c, snap)
    else:
        c.set_font(REG9)
        small_caps(c, 420, 112, "OUTLOOK")
        c.set_font(REG12)
        msg = "Outlook unavailable"
        c.set_cursor(420 + (330 - c.text_width(msg)) // 2, 200)
        c.write(msg)

    dashed_h(c, MARGIN_LEFT, MARGIN_RIGHT, 305)
    c.set_font(REG9)
    small_caps(c, 40, 324, "WEEK")
    if snap.forecast:
        draw_week(c, snap.forecast)

    dashed_h(c, MARGIN_LEFT, MARGIN_RIGHT, 455)
    c.set_font(REG9)
    left_w = small_caps_width(c, "TRAINS")
    small_caps(c, 40, 474, "TRAINS")
    arrow_x = 40 + left_w + 8
    right_arrow(c, arrow_x, 468)
    small_caps(c, arrow_x + 12, 474, "BREDA")

    draw_trains(c, snap.departures)
    draw_footer(c, snap)
    return c
