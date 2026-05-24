#!/usr/bin/env python3
"""
Downscale 64x64 1-bit PROGMEM icons in icons.h to 48x48 variants.

Reads the named 64x64 byte arrays from icons.h, nearest-neighbor downscales
them to 48x48, and writes new _48 variants in the same PROGMEM byte-array
format to icons_48.generated.h. Append manually to icons.h after eyeballing
the output (some glyphs may need hand-redraw — lightning and snowflake are
the usual culprits).

Bitmap convention matches icons.h: bit=1 is the background colour passed to
drawBitmap (WHITE in this project), bit=0 is the foreground (BLACK).
Each row is padded to a full byte; rows are MSB-first.

Run manually:
    python design/downscale_icons.py
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT = os.path.join(REPO_ROOT, "icons.h")
OUTPUT = os.path.join(REPO_ROOT, "design", "icons_48.generated.h")

# Week-strip uses day-only variants; that's all we need for now.
ICONS = [
    "icon_sun_64",
    "icon_cloud_sun_64",
    "icon_cloud_64",
    "icon_fog_64",
    "icon_rain_light_64",
    "icon_rain_64",
    "icon_rain_heavy_64",
    "icon_sun_rain_64",
    "icon_snow_64",
    "icon_sun_snow_64",
    "icon_lightning_64",
]

SRC = 64
DST = 48
SRC_BYTES_PER_ROW = SRC // 8         # 8
DST_BYTES_PER_ROW = DST // 8         # 6


def parse_icon(text, name):
    pattern = rf"const uint8_t PROGMEM {re.escape(name)}\[\]\s*=\s*\{{(.*?)\}};"
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    vals = [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", body)]
    if len(vals) != SRC * SRC_BYTES_PER_ROW:
        print(f"  WARN: {name} has {len(vals)} bytes (expected {SRC * SRC_BYTES_PER_ROW})", file=sys.stderr)
        return None
    return vals


def get_pixel(data, x, y):
    byte_idx = y * SRC_BYTES_PER_ROW + (x // 8)
    bit_idx = 7 - (x % 8)
    return (data[byte_idx] >> bit_idx) & 1


def downscale(data):
    out = []
    for oy in range(DST):
        # Nearest-neighbor: pick source row centered on the destination row.
        sy = (oy * SRC + SRC // 2) // DST
        if sy >= SRC:
            sy = SRC - 1
        for byte_start in range(0, DST, 8):
            b = 0
            for i in range(8):
                ox = byte_start + i
                sx = (ox * SRC + SRC // 2) // DST
                if sx >= SRC:
                    sx = SRC - 1
                b |= get_pixel(data, sx, sy) << (7 - i)
            out.append(b)
    return out


def emit(name, data, indent="\t"):
    lines = [f"const uint8_t PROGMEM {name}[] = {{"]
    for row_start in range(0, len(data), DST_BYTES_PER_ROW):
        row = data[row_start:row_start + DST_BYTES_PER_ROW]
        lines.append(indent + ", ".join(f"0x{b:02x}" for b in row) + ",")
    # Strip trailing comma on the last byte line
    lines[-1] = lines[-1].rstrip(",")
    lines.append("};")
    return "\n".join(lines)


def main():
    with open(INPUT, "r", encoding="utf-8") as f:
        text = f.read()

    out_chunks = [
        "// Auto-generated 48x48 downscales from icons.h via design/downscale_icons.py.",
        "// Nearest-neighbor; hand-redraw any glyph that looks bad before shipping.",
        "// To use: append the arrays below to icons.h (or #include this header).",
        "",
    ]
    for name in ICONS:
        data = parse_icon(text, name)
        if data is None:
            print(f"SKIP {name}")
            continue
        scaled = downscale(data)
        new_name = name.replace("_64", "_48")
        out_chunks.append(f"// '{new_name.replace('icon_', '').replace('_48', '')}', 48x48px - downscaled from 64x64")
        out_chunks.append(emit(new_name, scaled))
        out_chunks.append("")
        print(f"OK   {name} -> {new_name}")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out_chunks))

    print(f"\nWrote {OUTPUT}")
    print(f"({sum(DST * DST // 8 for _ in ICONS)} bytes of bitmap data)")


if __name__ == "__main__":
    main()
