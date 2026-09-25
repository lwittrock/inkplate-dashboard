"""The dashboard, drawn for the Inkplate's panel.

The design of September 2026: the bands of the firmware's original layout in
the same places, redrawn with Inter at any size, Material Symbols weather
icons, greys for everything secondary, and no boxes. (The exact port of the
firmware's own drawing, which matched the wall pixel for pixel, is in git
history: server/screen/render.py at commit c9a36d2.)

    y=0    masthead: greeting, date, sun or moon arc
    y=92   rule
    y=100  NOW (icon, temperature, feels like, wind) | the next 24 h or the rain
    y=302  hairline
    y=312  the week: day, icon, high, low
    y=452  hairline
    y=462  TRAINS → TILBURG UNI: three columns, no boxes
    y=580  footer: when it was drawn, battery

Drawn once in 8-bit grey at SCALE times the size, then finished for either
panel mode:
    grey()  the panel's 3-bit mode: anti-aliased, quantized to 8 levels
    mono()  the 1-bit mode: no anti-aliasing on text, secondary text in
            black, grey fills and hairlines as an ordered dither
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .headline import greeting
from .model import Category, Departure, DayForecast, Snapshot, Transfer, round_half_away
from .telemetry import battery_percent

ASSETS = Path(__file__).resolve().parent / "assets"

W, H = 800, 600
LEFT, RIGHT = 40, 760
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def level(i: int) -> int:
    """The panel's grey levels, 0 (black) to 7 (white)."""
    return round(i * 255 / 7)


@dataclass(frozen=True)
class Palette:
    text: int     # primary text and marks
    text2: int    # secondary text
    text3: int    # labels
    rule: int     # hairlines
    fill: int     # area fills (rain)
    faint: int    # the lightest tint (night in the chart, bar tracks)


# E-ink greys are less even than a screen's, so hierarchy comes from size and
# weight; text is never lighter than level 2, and the light greys are only for
# fills and hairlines.
GREY = Palette(text=0, text2=level(1), text3=level(2), rule=level(5), fill=level(4), faint=level(6))

# 1-bit: text stays black whatever its role (dithered text is unreadable);
# fills and hairlines keep their grey and become dither patterns.
MONO = Palette(text=0, text2=0, text3=0, rule=level(4), fill=level(4), faint=level(6))

# With crisp_small, text below this size is drawn without anti-aliasing even
# in greyscale: anti-aliased small type can look light and blurry on e-paper,
# while large type and curves gain from the smoothing. The service's
# SMALL_TEXT setting chooses; the real panel decides which reads better.
CRISP_BELOW = 14

TTF = ASSETS / "ttf" / "Inter-Variable.ttf"

# Weather icons: Material Symbols Rounded (Apache 2.0), a variable icon font
# drawn like text. Subset to the glyphs material_name() uses, fill and grade
# pinned at 0, weight and optical size still variable: tools/subset_icons.py.
MATERIAL_TTF = str(ASSETS / "ttf" / "MaterialSymbolsRounded-weather.ttf")
_MATERIAL_CP = {}


def _material_char(name: str) -> str:
    if not _MATERIAL_CP:
        for line in open(MATERIAL_TTF.replace(".ttf", ".codepoints"), encoding="utf-8"):
            k, v = line.split()
            _MATERIAL_CP[k] = chr(int(v, 16))
    return _MATERIAL_CP[name]


@lru_cache(maxsize=None)
def _icon_font(px: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(MATERIAL_TTF, px)
    f.set_variation_by_axes([min(max(px, 20), 48), weight])   # opsz, wght
    return f


def material_name(cat: Category, night: bool) -> str:
    C = Category
    if cat == C.CLEAR:
        return "clear_night" if night else "sunny"
    if cat == C.PARTLY_CLOUDY:
        return "partly_cloudy_night" if night else "partly_cloudy_day"
    # One rain glyph: Material's light and heavy variants are bare streaks
    # without a cloud and break the set. The headline and the rain chart say how much.
    # Sun and showers by day is drawn from two glyphs (_showers_mask); at night it is rain.
    return {C.OVERCAST: "cloud", C.FOG: "foggy", C.DRIZZLE: "rainy", C.RAIN: "rainy",
            C.RAIN_HEAVY: "rainy", C.SHOWERS: "rainy", C.SNOW: "weather_snowy",
            C.THUNDERSTORM: "thunderstorm"}.get(cat, "cloud")


# Sun and showers: Material Symbols has no sun-with-rain glyph, so the rain
# cloud goes bottom left and a smaller, bolder sun top right, knocked out
# around the cloud's silhouette plus a gap. Fractions of the icon's box.
SHOWERS_CLOUD = (0.86, 0.38, 0.64)      # size, centre x, centre y
SHOWERS_SUN = (0.64, 0.66, 0.30)
SHOWERS_GAP = 0.06


def _knock_out(sun: Image.Image, keepout: Image.Image) -> Image.Image:
    """The sun minus the keepout. The disc (the largest piece) is cut into an
    arc, but a ray the keepout touches goes whole: a sliver of one reads as a speck."""
    labels = sun.point(lambda v: 255 if v >= 128 else 0)
    px = labels.load()
    pieces = []
    for y in range(labels.height):
        for x in range(labels.width):
            if px[x, y] == 255:
                pieces.append(len(pieces) + 1)
                ImageDraw.floodfill(labels, (x, y), pieces[-1])
    masks = [labels.point(lambda v, n=n: 255 if v == n else 0) for n in pieces]
    disc = max(masks, key=lambda m: m.histogram()[255], default=None)
    for m in masks:
        if m is not disc and ImageChops.multiply(m, keepout).getbbox():
            # Grown a little, so the anti-aliased fringe goes with it.
            keepout = ImageChops.lighter(keepout, m.filter(ImageFilter.MaxFilter(5)))
    return ImageChops.subtract(sun, keepout)


@lru_cache(maxsize=None)
def _showers_mask(px: int, em: int, weight: int, mono: bool) -> Image.Image:
    """The icon as a mask for a px-square box, with a px // 4 margin on every
    side (the sun's rays may reach past the box, as Material's own do)."""
    pad = px // 4
    size_px = px + 2 * pad

    def glyph(name, part, wght):
        size, cx, cy = part
        m = Image.new("L", (size_px, size_px), 0)
        d = ImageDraw.Draw(m)
        d.fontmode = "1" if mono else "L"
        d.text((pad + cx * px, pad + cy * px), _material_char(name),
               font=_icon_font(round(em * size), wght), fill=255, anchor="mm")
        return m

    cloud = glyph("rainy", SHOWERS_CLOUD, weight)
    # The sun's strokes are thinner at its smaller size; a heavier weight evens them out.
    sun = glyph("sunny", SHOWERS_SUN, weight + 200)
    # The silhouette is whatever a flood from the corner can't reach: the outline and its inside.
    solid = cloud.point(lambda v: 255 if v >= 128 else 0)
    ImageDraw.floodfill(solid, (size_px - 1, size_px - 1), 128)
    silhouette = solid.point(lambda v: 0 if v == 128 else 255)
    gap = max(1.0, SHOWERS_GAP * px)
    keepout = silhouette.filter(ImageFilter.GaussianBlur(gap)).point(lambda v: 255 if v > 4 else 0)
    return ImageChops.lighter(cloud, _knock_out(sun, keepout))


@lru_cache(maxsize=None)
def _font(px: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(TTF), px)
    # Inter's optical size follows the point size: text cut small, display cut large.
    f.set_variation_by_axes([min(max(px, 14), 32), weight])
    return f


def temp_text(t: float) -> str:
    v = round_half_away(t)
    return f"{v}°" if v >= 0 else f"−{-v}°"


class Canvas:
    def __init__(self, mono: bool, crisp_small: bool = False) -> None:
        self.mono = mono
        self.crisp_below = CRISP_BELOW if crisp_small and not mono else 0
        self.s = 1 if mono else 3                   # supersampling for anti-aliasing
        self.p = MONO if mono else GREY
        self.img = Image.new("L", (W * self.s, H * self.s), 255)
        self.d = ImageDraw.Draw(self.img)
        self.d.fontmode = "1" if mono else "L"
        self.crisp: list = []      # small text for grey(): (x, y, s, size, weight, fill, anchor)

    # --- helpers in screen pixels ---------------------------------------------

    def font(self, size: float, weight: int) -> ImageFont.FreeTypeFont:
        return _font(round(size * self.s), weight)

    def text(self, x, y, s, size, weight=400, fill=None, anchor="ls") -> float:
        """Draw at a baseline; returns the width in screen pixels."""
        fill = self.p.text if fill is None else fill
        if size < self.crisp_below:
            self.crisp.append((x, y, s, size, weight, fill, anchor))
            return _font(round(size), weight).getlength(s)
        f = self.font(size, weight)
        self.d.text((x * self.s, y * self.s), s, font=f, fill=fill, anchor=anchor)
        return self.d.textlength(s, font=f) / self.s

    def width(self, s, size, weight=400) -> float:
        if size < self.crisp_below:
            return _font(round(size), weight).getlength(s)
        return self.d.textlength(s, font=self.font(size, weight)) / self.s

    def caps(self, x, y, s, size=13, weight=600, fill=None, tracking=0.12, right=False) -> None:
        """Letter-spaced capitals for section labels."""
        gap = size * tracking
        total = sum(self.width(ch, size, weight) for ch in s) + gap * (len(s) - 1)
        cx = x - total if right else x
        for ch in s:
            cx += self.text(cx, y, ch, size, weight, fill=self.p.text3 if fill is None else fill) + gap

    def line(self, pts, width=1.0, fill=None) -> None:
        self.d.line([(x * self.s, y * self.s) for x, y in pts],
                    fill=self.p.text if fill is None else fill,
                    width=max(1, round(width * self.s)), joint="curve")

    def rect(self, x0, y0, x1, y1, fill, radius=0.0) -> None:
        box = (x0 * self.s, y0 * self.s, x1 * self.s - 1, y1 * self.s - 1)
        if box[2] < box[0] or box[3] < box[1]:
            return      # under a pixel wide, e.g. the battery fill at 5%; Pillow raises
        if radius:
            self.d.rounded_rectangle(box, radius=radius * self.s, fill=fill)
        else:
            self.d.rectangle(box, fill=fill)

    def disc(self, cx, cy, r, fill) -> None:
        self.d.ellipse(((cx - r) * self.s, (cy - r) * self.s, (cx + r) * self.s, (cy + r) * self.s),
                       fill=fill)

    def polygon(self, pts, fill) -> None:
        self.d.polygon([(x * self.s, y * self.s) for x, y in pts], fill=fill)

    def weather_icon(self, cat, x: int, y: int, box: int, night=False) -> None:
        """A weather icon filling a box px square at (x, y); lighter at large sizes."""
        em, weight = round(box * 1.12 * self.s), 300 if box > 64 else 400
        if cat == Category.SHOWERS and not night:
            mask = _showers_mask(box * self.s, em, weight, self.mono)
            pad = box * self.s // 4
            self.img.paste(self.p.text, (x * self.s - pad, y * self.s - pad), mask)
            return
        self.d.text(((x + box / 2) * self.s, (y + box / 2) * self.s),
                    _material_char(material_name(cat, night)), font=_icon_font(em, weight),
                    fill=self.p.text, anchor="mm")

    # --- finishing ---------------------------------------------------------------

    def grey(self) -> Image.Image:
        img = self.img.resize((W, H), Image.BOX) if self.s != 1 else self.img
        img = img.point(lambda v: level(round(v / 255 * 7)))
        d = ImageDraw.Draw(img)
        d.fontmode = "1"
        for x, y, s, size, weight, fill, anchor in self.crisp:
            d.text((round(x), round(y)), s, font=_font(round(size), weight), fill=fill, anchor=anchor)
        return img

    def mono_image(self) -> Image.Image:
        """Ordered (Bayer 4x4) dither: pure black and white stay exact."""
        bayer = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
        tile = Image.new("L", (4, 4))
        tile.putdata([round((b + 0.5) * 16) for b in bayer])
        thresh = Image.new("L", (W, H))
        for ty in range(0, H, 4):
            for tx in range(0, W, 4):
                thresh.paste(tile, (tx, ty))
        darker = ImageChops.subtract(thresh, self.img)     # > 0 where pixel < threshold
        return darker.point(lambda v: 0 if v > 0 else 255).convert("1", dither=Image.Dither.NONE)


# --- masthead (y=0-92) ------------------------------------------------------------

def masthead(c: Canvas, snap: Snapshot, night: bool) -> None:
    now, fc = snap.now, snap.forecast
    current = snap.weather.category if snap.weather else None
    c.text(LEFT, 50, greeting(now, fc[0] if fc else None, current), 34, 650)
    c.text(LEFT + 1, 77, f"{now.day} {MONTHS[now.month - 1]}", 16, 420, fill=c.p.text2)

    c.rect(LEFT, 91, RIGHT, 93, c.p.text)

    # Sun (or moon) on its arc: travelled part solid, the rest a hairline.
    x0, x1, base, rise = 604, 760, 60, 30
    if not fc or not fc[0].sunrise or not fc[0].sunset:
        return
    def minutes(hhmm): return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    now_min = now.hour * 60 + now.minute
    if not night:
        start, end = minutes(fc[0].sunrise), minutes(fc[0].sunset)
        left, right = fc[0].sunrise, fc[0].sunset
    elif now_min < minutes(fc[0].sunrise):
        # Before sunrise: the night began at yesterday's sunset, about today's time.
        start, end = minutes(fc[0].sunset) - 24 * 60, minutes(fc[0].sunrise)
        left, right = fc[0].sunset, fc[0].sunrise
    else:
        rise_next = fc[1].sunrise if len(fc) > 1 and fc[1].sunrise else fc[0].sunrise
        start, end = minutes(fc[0].sunset), minutes(rise_next) + 24 * 60
        left, right = fc[0].sunset, rise_next
    t = min(max((now_min - start) / max(end - start, 1), 0.0), 1.0)

    def point(u):
        return x0 + u * (x1 - x0), base - rise * math.sin(math.pi * u)

    c.line([(x0 - 6, base), (x1 + 6, base)], 1, c.p.rule)
    steps = 60
    c.line([point(i / steps) for i in range(steps + 1)], 1.2, c.p.rule)
    c.line([point(t * i / steps) for i in range(steps + 1)], 2.2)
    px, py = point(t)
    if night:
        c.disc(px, py, 9, 255)
        c.disc(px, py, 7, c.p.text)
        c.disc(px + 3.5, py - 2.5, 6, 255)
    else:
        c.disc(px, py, 7.5, 255)
        c.disc(px, py, 5.5, c.p.text)
    c.text(x0 - 6, 82, left, 13.5, 450, fill=c.p.text2)
    c.text(x1 + 6, 82, right, 13.5, 450, fill=c.p.text2, anchor="rs")


# --- now (y=100-296), left ---------------------------------------------------------

def wind_arrow(c: Canvas, cx: float, cy: float, bearing: int, r: float = 8) -> None:
    """Points where the wind blows to."""
    a = math.radians(bearing + 180)
    dx, dy = math.sin(a), -math.cos(a)
    tip = (cx + dx * r, cy + dy * r)
    tail = (cx - dx * r, cy - dy * r)
    c.line([tail, (cx + dx * (r - 5), cy + dy * (r - 5))], 2.2)
    px, py = -dy, dx
    c.polygon([tip, (tip[0] - dx * 7 + px * 4.5, tip[1] - dy * 7 + py * 4.5),
               (tip[0] - dx * 7 - px * 4.5, tip[1] - dy * 7 - py * 4.5)], c.p.text)


def now_block(c: Canvas, snap: Snapshot, night: bool) -> None:
    c.caps(LEFT, 120, "NOW")
    w = snap.weather
    if not w:
        c.text(LEFT, 200, "Current weather unavailable", 17, 450, fill=c.p.text2)
        return
    c.weather_icon(w.category, 32, 134, 128, night=night)
    x = 186
    c.text(x - 5, 222, temp_text(w.temp), 100, 600)
    if w.feels is not None:
        c.text(x, 254, f"Feels like {temp_text(w.feels)}", 17, 450, fill=c.p.text2)
    wind_arrow(c, x + 8, 278, w.wind_bearing)
    c.text(x + 24, 284, f"{round(w.wind_kmh)} km/h", 17, 450, fill=c.p.text2)


# --- now (y=100-296), right: the next 24 hours, or the rain -------------------------

X0, X1, YT, YB = 420, 760, 138, 258


def catmull_rom(pts, steps=8):
    out = []
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t
                                     + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                     + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    out.append(pts[-1])
    return out


def nights(snap: Snapshot, start: datetime, end: datetime):
    """(from, to) spans of darkness between start and end, from the forecast."""
    fc = snap.forecast
    spans = []
    for d in range(-1, min(len(fc), 3)):
        day = start.date() + timedelta(days=d)
        today = fc[d] if 0 <= d < len(fc) else None
        tomorrow = fc[d + 1] if 0 <= d + 1 < len(fc) else None
        sunset = today.sunset if today else (fc[0].sunset if fc else "")
        sunrise = tomorrow.sunrise if tomorrow else (fc[0].sunrise if fc else "")
        if not sunset or not sunrise:
            continue
        a = datetime.combine(day, datetime.strptime(sunset, "%H:%M").time())
        b = datetime.combine(day + timedelta(days=1), datetime.strptime(sunrise, "%H:%M").time())
        a, b = max(a, start), min(b, end)
        if a < b:
            spans.append((a, b))
    return spans


def temp_chart(c: Canvas, snap: Snapshot) -> None:
    """From now to 23 hours on: Open-Meteo's list starts at the top of this
    hour, so the first point is interpolated to the current minute."""
    hourly = snap.hourly
    t0 = snap.now.replace(minute=0, second=0, microsecond=0)
    frac = (snap.now - t0).total_seconds() / 3600
    first = hourly[0] + (hourly[1] - hourly[0]) * frac
    series = [(snap.now, first)] + [(t0 + timedelta(hours=h), v)
                                   for h, v in enumerate(hourly) if h > frac]
    start, end = series[0][0], series[-1][0]
    span = (end - start).total_seconds()

    def x_at(when: datetime) -> float:
        return X0 + (when - start).total_seconds() / span * (X1 - X0)

    temps = [v for _, v in series]
    lo, hi = min(temps), max(temps)
    pad = max(1.5, (hi - lo) * 0.15)
    lo, hi = lo - pad, hi + pad

    def y_of(v: float) -> float:
        return YB - 4 - (v - lo) / (hi - lo) * (YB - YT - 22)

    for a, b in nights(snap, start, end):
        c.rect(x_at(a), YT, x_at(b), YB, c.p.faint)
    c.line([(X0, YB), (X1, YB)], 1, c.p.rule)

    pts = [(x_at(t), y_of(v)) for t, v in series]
    c.line(catmull_rom(pts), 2.4)

    # The extremes, labels kept inside the chart; "now" is the left edge.
    for idx in sorted({temps.index(max(temps)), temps.index(min(temps))}):
        px, py = pts[idx]
        c.disc(px, py, 5.5, 255)
        c.disc(px, py, 3.6, c.p.text)
        c.text(min(max(px, X0 + 14), X1 - 14), py - 10, temp_text(temps[idx]), 14.5, 650, anchor="ms")

    c.text(X0, 280, "now", 13, 600, anchor="ls")
    hour = t0 + timedelta(hours=1)
    while hour < end:
        if hour.hour % 6 == 0:
            x = x_at(hour)
            if X0 + 40 < x < X1 - 20:
                c.line([(x, YB), (x, YB + 4)], 1, c.p.rule)
                c.text(x, 280, f"{hour:%H}:00", 13, 450, fill=c.p.text2, anchor="ms")
        hour += timedelta(hours=1)


def rain_chart(c: Canvas, snap: Snapshot) -> None:
    rain = snap.rain[:24]
    n = len(rain)
    top = max(12.0, max(s.mmh for s in rain) * 1.1)

    def y_of(mm: float) -> float:
        # Square-root scale: drizzle is visible, a downpour still fits.
        return YB - math.sqrt(min(mm, top) / top) * (YB - YT)

    for mm, label in ((2.5, "moderate"), (10.0, "heavy")):
        y = y_of(mm)
        c.line([(X0, y), (X1, y)], 1, c.p.rule)
        c.text(X1, y - 4, label, 13, 450, fill=c.p.text3, anchor="rs")

    pts = [(X0 + i / (n - 1) * (X1 - X0), y_of(s.mmh)) for i, s in enumerate(rain)]
    curve = [(x, min(y, YB)) for x, y in catmull_rom(pts)]
    c.polygon(curve + [(X1, YB), (X0, YB)], c.p.fill)
    c.line(curve, 2.2)
    c.line([(X0, YB), (X1, YB)], 1, c.p.text)

    c.text(X0, 280, "now", 13, 600, anchor="ls")
    for i, s in enumerate(rain):
        if s.label.endswith((":00", ":30")) and 3 < i < n - 1:
            x = pts[i][0]
            c.line([(x, YB), (x, YB + 4)], 1, c.p.rule)
            c.text(x, 280, s.label, 13, 450, fill=c.p.text2, anchor="ms")


def rain_summary(snap: Snapshot) -> str:
    wet = [s.mmh >= 0.4 for s in snap.rain]
    if not any(wet):
        return "dry for two hours"
    first = wet.index(True)
    if first > 0:
        return f"rain from {snap.rain[first].label}"
    stop = next((i for i, w in enumerate(wet) if not w), None)
    return f"raining, until {snap.rain[stop].label}" if stop else "raining for two hours"


def outlook(c: Canvas, snap: Snapshot) -> None:
    raining = any(s.mmh >= 0.4 for s in snap.rain)
    if raining:
        c.caps(X0, 120, "RAIN, NEXT 2 HOURS")
        c.text(X1, 120, rain_summary(snap), 13.5, 600, anchor="rs")
        rain_chart(c, snap)
    elif len(snap.hourly) >= 2:
        c.caps(X0, 120, "NEXT 24 HOURS")
        if snap.rain:
            c.text(X1, 120, rain_summary(snap), 13.5, 450, fill=c.p.text2, anchor="rs")
        temp_chart(c, snap)
    else:
        c.caps(X0, 120, "OUTLOOK")
        c.text(X0, 200, "Forecast unavailable", 17, 450, fill=c.p.text2)


# --- the week (y=312-444) -------------------------------------------------------------

def week(c: Canvas, fc: list[DayForecast]) -> None:
    """A column per day: name, icon, the high big, the low small beneath."""
    days = fc[:7]
    if not days:
        c.text(LEFT, 380, "Week forecast unavailable", 17, 450, fill=c.p.text2)
        return
    cw = (RIGHT - LEFT) / 7
    for i, d in enumerate(days):
        cx = LEFT + cw * (i + 0.5)
        today = i == 0
        c.text(cx, 332, d.day_name, 15.5, 700 if today else 500,
               fill=c.p.text if today else c.p.text2, anchor="ms")
        c.weather_icon(d.category, round(cx - 24), 340, 48)
        c.text(cx, 416, temp_text(d.temp_max), 21, 650, anchor="ms")
        c.text(cx, 440, temp_text(d.temp_min), 16, 450, fill=c.p.text2, anchor="ms")


# --- trains (y=462-570) ------------------------------------------------------------------

def trains(c: Canvas, snap: Snapshot) -> None:
    c.caps(LEFT, 476, "TRAINS → TILBURG UNI")
    deps = snap.departures[:3]
    if not deps:
        msg = "No trains in the next three hours" if snap.trains_ok else "Train times unavailable"
        c.text(LEFT, 530, msg, 17, 450, fill=c.p.text2)
        return
    col = (RIGHT - LEFT) / 3
    for i, d in enumerate(deps):
        x0 = LEFT + i * col
        if i:
            c.line([(x0, 490), (x0, 570)], 1, c.p.rule)
        train(c, x0 + (16 if i else 0), x0 + col - 16, d)


def train(c: Canvas, x: float, right: float, d: Departure) -> None:
    """Three rows: what's wrong (or "on time", and "from HS" when the picker
    swapped in a Den Haag HS train), the time and platform, the arrival."""
    rx = x
    if d.origin == "HS":
        w = c.width("from HS", 13, 700) + 12
        c.rect(x, 489, x + w, 506, c.p.text, radius=3)
        c.text(x + 6, 502, "from HS", 13, 700, fill=255)
        rx += w + 8
    if d.cancelled:
        c.text(rx, 502, "cancelled", 14.5, 700)
    elif d.delay_min:
        c.text(rx, 502, f"+{d.delay_min} min late", 14.5, 700)
    else:
        c.text(rx, 502, "on time", 14.5, 450, fill=c.p.text2)

    tw = c.text(x, 541, d.time, 37, 650, fill=c.p.text3 if d.cancelled else c.p.text)
    if d.cancelled:
        c.line([(x - 2, 528), (x + tw + 2, 528)], 2.6)
    c.text(right, 541, d.track, 24, 650, anchor="rs")
    c.text(right - c.width(d.track, 24, 650) - 6, 541, "platform", 13, 450,
           fill=c.p.text3, anchor="rs")

    if d.cancelled:
        return
    if d.transfer == Transfer.CANCELLED:
        c.text(x, 566, "→ transfer at Breda cancelled", 14.5, 700)
        return
    ax = x + c.text(x, 566, f"→ Uni {d.uni_arr}", 14.5, 450, fill=c.p.text2)
    if d.transfer == Transfer.LATE:
        c.text(ax, 566, "  ·  transfer late", 14.5, 700)


# --- footer (y=580-596) --------------------------------------------------------------------

def footer(c: Canvas, snap: Snapshot) -> None:
    c.text(LEFT, 594, f"Updated {snap.now:%H:%M}", 13, 450, fill=c.p.text3)
    pct = battery_percent(snap.battery_v)
    bx, by, bw, bh = RIGHT - 28, 583, 25, 12
    c.rect(bx, by, bx + bw, by + bh, c.p.text, radius=2.5)
    c.rect(bx + 1.5, by + 1.5, bx + bw - 1.5, by + bh - 1.5, 255, radius=1.5)
    c.rect(bx + bw + 1, by + 3.5, bx + bw + 3, by + bh - 3.5, c.p.text)
    if pct:
        c.rect(bx + 3, by + 3, bx + 3 + (bw - 6) * pct / 100, by + bh - 3, c.p.text, radius=1)
    # No percentage: the generic LiPo curve and a single ADC sample can't back one
    # up yet (docs/server-rendering-design.md, step 7). The version shows only on
    # local "dev" builds, for the bench; releases are in HA's firmware sensor.
    if snap.firmware == "dev":
        c.text(bx - 8, 594, snap.firmware, 13, 450, fill=c.p.text3, anchor="rs")


# --- the whole screen ------------------------------------------------------------------------

def hhmm(s: str) -> int:
    """"HH:MM" -> minutes since midnight, -1 on bad input."""
    return int(s[:2]) * 60 + int(s[3:5]) if s and len(s) >= 5 and s[2] == ":" else -1


def is_night(snap: Snapshot) -> bool:
    """Before today's sunrise or after its sunset; 20:00-06:00 without a forecast."""
    now_min = snap.now.hour * 60 + snap.now.minute
    fc = snap.forecast
    sr = hhmm(fc[0].sunrise) if fc else -1
    ss = hhmm(fc[0].sunset) if fc else -1
    if sr >= 0 and ss > sr:
        return now_min < sr or now_min >= ss
    return snap.now.hour >= 20 or snap.now.hour < 6


# --- drawing it -------------------------------------------------------------------------

def draw(snap: Snapshot, mono: bool, crisp_small: bool = False) -> Canvas:
    c = Canvas(mono, crisp_small)
    night = is_night(snap)
    masthead(c, snap, night)
    now_block(c, snap, night)
    outlook(c, snap)
    c.line([(LEFT, 302), (RIGHT, 302)], 1, c.p.rule)
    week(c, snap.forecast)
    c.line([(LEFT, 452), (RIGHT, 452)], 1, c.p.rule)
    trains(c, snap)
    footer(c, snap)
    return c


def render_grey(snap: Snapshot, crisp_small: bool = False) -> Image.Image:
    return draw(snap, mono=False, crisp_small=crisp_small).grey()


def render_mono(snap: Snapshot) -> Image.Image:
    return draw(snap, mono=True).mono_image()
