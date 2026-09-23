"""A 1-bit canvas that draws exactly like Adafruit GFX on the Inkplate.

The dashboard's layout was tuned pixel by pixel against the firmware's
drawing library. This module reproduces those routines (Bresenham lines,
the midpoint circle fill, the triangle scanline fill, GFX-font text and
its bounds) with the same integer arithmetic, so the ported layout lands
on the same pixels. C truncates integer division and float-to-int casts
toward zero; `cdiv` and `int()` do the same here, where Python's `//`
would floor.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

WIDTH, HEIGHT = 800, 600
BLACK, WHITE = 1, 0

ASSETS = Path(__file__).resolve().parent / "assets"


def cdiv(a: int, b: int) -> int:
    """C integer division: truncates toward zero."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


@dataclass(frozen=True)
class Font:
    first: int
    last: int
    y_advance: int
    glyphs: tuple  # (bitmapOffset, width, height, xAdvance, xOffset, yOffset)
    bitmap: bytes


@lru_cache(maxsize=None)
def font(name: str) -> Font:
    raw = json.loads((ASSETS / "fonts" / f"{name}.json").read_text())
    return Font(
        first=raw["first"],
        last=raw["last"],
        y_advance=raw["yAdvance"],
        glyphs=tuple(tuple(g) for g in raw["glyphs"]),
        bitmap=bytes.fromhex(raw["bitmap"]),
    )


@lru_cache(maxsize=None)
def icon(name: str) -> tuple[int, int, bytes]:
    """(width, height, pixels) with one byte per pixel, BLACK or WHITE."""
    img = Image.open(ASSETS / "icons" / f"{name}.png").convert("L")
    return img.width, img.height, bytes(BLACK if p < 128 else WHITE for p in img.getdata())


class Canvas:
    def __init__(self) -> None:
        self.px = bytearray(WIDTH * HEIGHT)  # WHITE everywhere
        self.cursor_x = 0
        self.cursor_y = 0
        self.text_color = BLACK
        self.font: Font | None = None

    # --- pixels and lines -------------------------------------------------

    def pixel(self, x: int, y: int, color: int = BLACK) -> None:
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            self.px[y * WIDTH + x] = color

    def hline(self, x: int, y: int, w: int, color: int = BLACK) -> None:
        for i in range(w):
            self.pixel(x + i, y, color)

    def vline(self, x: int, y: int, h: int, color: int = BLACK) -> None:
        for j in range(h):
            self.pixel(x, y + j, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: int = BLACK) -> None:
        if x0 == x1:
            if y0 > y1:
                y0, y1 = y1, y0
            self.vline(x0, y0, y1 - y0 + 1, color)
            return
        if y0 == y1:
            if x0 > x1:
                x0, x1 = x1, x0
            self.hline(x0, y0, x1 - x0 + 1, color)
            return
        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0 = y0, x0
            x1, y1 = y1, x1
        if x0 > x1:
            x0, x1 = x1, x0
            y0, y1 = y1, y0
        dx = x1 - x0
        dy = abs(y1 - y0)
        err = dx // 2
        ystep = 1 if y0 < y1 else -1
        while x0 <= x1:
            if steep:
                self.pixel(y0, x0, color)
            else:
                self.pixel(x0, y0, color)
            err -= dy
            if err < 0:
                y0 += ystep
                err += dx
            x0 += 1

    # --- rectangles, circles, triangles ------------------------------------

    def rect(self, x: int, y: int, w: int, h: int, color: int = BLACK) -> None:
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int = BLACK) -> None:
        for i in range(x, x + w):
            self.vline(i, y, h, color)

    def fill_circle(self, x0: int, y0: int, r: int, color: int = BLACK) -> None:
        self.vline(x0, y0 - r, 2 * r + 1, color)
        # fillCircleHelper(x0, y0, r, corners=3, delta=0)
        f = 1 - r
        ddf_x = 1
        ddf_y = -2 * r
        x, y = 0, r
        px, py = x, y
        delta = 1
        while x < y:
            if f >= 0:
                y -= 1
                ddf_y += 2
                f += ddf_y
            x += 1
            ddf_x += 2
            f += ddf_x
            if x < y + 1:
                self.vline(x0 + x, y0 - y, 2 * y + delta, color)
                self.vline(x0 - x, y0 - y, 2 * y + delta, color)
            if y != py:
                self.vline(x0 + py, y0 - px, 2 * px + delta, color)
                self.vline(x0 - py, y0 - px, 2 * px + delta, color)
                py = y
            px = x

    def fill_triangle(self, x0, y0, x1, y1, x2, y2, color: int = BLACK) -> None:
        if y0 > y1:
            y0, y1 = y1, y0
            x0, x1 = x1, x0
        if y1 > y2:
            y2, y1 = y1, y2
            x2, x1 = x1, x2
        if y0 > y1:
            y0, y1 = y1, y0
            x0, x1 = x1, x0

        if y0 == y2:
            a = b = x0
            if x1 < a:
                a = x1
            elif x1 > b:
                b = x1
            if x2 < a:
                a = x2
            elif x2 > b:
                b = x2
            self.hline(a, y0, b - a + 1, color)
            return

        dx01, dy01 = x1 - x0, y1 - y0
        dx02, dy02 = x2 - x0, y2 - y0
        dx12, dy12 = x2 - x1, y2 - y1
        sa = sb = 0
        last = y1 if y1 == y2 else y1 - 1
        y = y0
        while y <= last:
            a = x0 + cdiv(sa, dy01)
            b = x0 + cdiv(sb, dy02)
            sa += dx01
            sb += dx02
            if a > b:
                a, b = b, a
            self.hline(a, y, b - a + 1, color)
            y += 1
        sa = dx12 * (y - y1)
        sb = dx02 * (y - y0)
        while y <= y2:
            a = x1 + cdiv(sa, dy12)
            b = x0 + cdiv(sb, dy02)
            sa += dx12
            sb += dx02
            if a > b:
                a, b = b, a
            self.hline(a, y, b - a + 1, color)
            y += 1

    # --- bitmaps -------------------------------------------------------------

    def draw_icon(self, x: int, y: int, name: str) -> None:
        """Opaque blit, as drawBitmap(x, y, bmp, w, h, WHITE, BLACK) does."""
        w, h, data = icon(name)
        for j in range(h):
            row = j * w
            for i in range(w):
                self.pixel(x + i, y + j, data[row + i])

    # --- text ---------------------------------------------------------------

    def set_font(self, name: str) -> None:
        self.font = font(name)

    def set_cursor(self, x: int, y: int) -> None:
        self.cursor_x, self.cursor_y = x, y

    def _draw_char(self, x: int, y: int, c: int, color: int) -> None:
        f = self.font
        bo, w, h, _, xo, yo = f.glyphs[c - f.first]
        bits = bit = 0
        for yy in range(h):
            for xx in range(w):
                if not (bit & 7):
                    bits = f.bitmap[bo]
                    bo += 1
                bit += 1
                if bits & 0x80:
                    self.pixel(x + xo + xx, y + yo + yy, color)
                bits = (bits << 1) & 0xFF

    def write(self, text: str) -> None:
        """print(): draws at the cursor and advances it, wrapping at the edge."""
        f = self.font
        for ch in text:
            c = ord(ch)
            if ch == "\n":
                self.cursor_x = 0
                self.cursor_y += f.y_advance
                continue
            if ch == "\r" or not (f.first <= c <= f.last):
                continue
            _, w, h, xa, xo, _ = f.glyphs[c - f.first]
            if w > 0 and h > 0:
                if self.cursor_x + (xo + w) > WIDTH:
                    self.cursor_x = 0
                    self.cursor_y += f.y_advance
                self._draw_char(self.cursor_x, self.cursor_y, c, self.text_color)
            self.cursor_x += xa

    def text_bounds(self, text: str, x: int = 0, y: int = 0) -> tuple[int, int, int, int]:
        """getTextBounds(): (x1, y1, w, h) of the drawn pixels."""
        f = self.font
        bx, by, bw, bh = x, y, 0, 0
        minx = miny = 0x7FFF
        maxx = maxy = -1
        for ch in text:
            c = ord(ch)
            if ch == "\n":
                x = 0
                y += f.y_advance
                continue
            if ch == "\r" or not (f.first <= c <= f.last):
                continue
            _, gw, gh, xa, xo, yo = f.glyphs[c - f.first]
            if x + (xo + gw) > WIDTH:
                x = 0
                y += f.y_advance
            x1, y1 = x + xo, y + yo
            x2, y2 = x1 + gw - 1, y1 + gh - 1
            minx, miny = min(minx, x1), min(miny, y1)
            maxx, maxy = max(maxx, x2), max(maxy, y2)
            x += xa
        if maxx >= minx:
            bx, bw = minx, maxx - minx + 1
        if maxy >= miny:
            by, bh = miny, maxy - miny + 1
        return bx, by, bw, bh

    def text_width(self, text: str) -> int:
        return self.text_bounds(text)[2]

    # --- output -------------------------------------------------------------

    def to_image(self) -> Image.Image:
        """Mode "1" image, black ink on white, as the panel shows it."""
        lum = bytes(self.px).translate(bytes([255, 0]) + bytes(254))
        return Image.frombytes("L", (WIDTH, HEIGHT), lum).convert("1", dither=Image.Dither.NONE)

    def to_frame(self) -> bytes:
        """The 60,000-byte wire format: rows of 100 bytes, MSB first, 1 = black."""
        packed = self.to_image().tobytes()  # PIL: 1 = white
        return bytes(b ^ 0xFF for b in packed)
