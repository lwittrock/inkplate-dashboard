"""Convert the firmware's GFX font headers and icon bitmaps into server assets.

One-off (re-runnable) import so the server draws text and icons exactly as the
device does today. Reads ../Fonts/*.h and ../icons.h relative to server/, writes
screen/assets/fonts/<name>.json and screen/assets/icons/<name>.png.

    python tools/import_gfx_assets.py

The firmware copies go away when the thin client ships; the assets written here
are then the only copy, so they are committed.
"""

import json
import re
import sys
from pathlib import Path

from PIL import Image

SERVER = Path(__file__).resolve().parent.parent
REPO = SERVER.parent
ASSETS = SERVER / "screen" / "assets"

FONTS = [
    "Inter_Regular9pt7b",
    "Inter_Regular12pt7b",
    "Inter_Regular18pt7b",
    "Inter_Bold9pt7b",
    "Inter_Bold12pt7b",
    "Inter_Bold18pt7b",
    "Inter_Bold48pt7b",
]

HEX = re.compile(r"0x([0-9A-Fa-f]{2})")


def c_array_body(source: str, name: str) -> str:
    """Text between the braces of `... name[] ... = { ... };`."""
    m = re.search(re.escape(name) + r"\[\][^=]*=\s*\{(.*?)\};", source, re.S)
    if not m:
        sys.exit(f"array {name} not found")
    return m.group(1)


def import_font(name: str) -> None:
    src = (REPO / "Fonts" / f"{name}.h").read_text()
    bitmap = bytes(int(h, 16) for h in HEX.findall(c_array_body(src, name + "Bitmaps")))
    glyph_body = c_array_body(src, name + "Glyphs")
    # Strip the "// 0x20 ' '" comments first: some of them contain braces.
    glyph_body = re.sub(r"//[^\n]*", "", glyph_body)
    glyphs = [
        [int(v) for v in g.split(",")]
        for g in re.findall(r"\{([^{}]*)\}", glyph_body)
    ]
    m = re.search(r"GFXfont\s+" + re.escape(name) + r"[^=]*=\s*\{.*?,.*?,\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),\s*(\d+)\s*\}", src, re.S)
    if not m:
        sys.exit(f"GFXfont {name} not found")
    first, last, y_advance = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3))
    if len(glyphs) != last - first + 1 or any(len(g) != 6 for g in glyphs):
        sys.exit(f"{name}: unexpected glyph table shape")
    out = {
        "first": first,
        "last": last,
        "yAdvance": y_advance,
        # [bitmapOffset, width, height, xAdvance, xOffset, yOffset]
        "glyphs": glyphs,
        "bitmap": bitmap.hex(),
    }
    path = ASSETS / "fonts" / f"{name}.json"
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    print(f"{path.relative_to(SERVER)}: {len(glyphs)} glyphs, {len(bitmap)} bytes")


def import_icons() -> None:
    src = (REPO / "icons.h").read_text()
    for m in re.finditer(r"const uint8_t PROGMEM (icon_(\w+)_(128|48))\[\]\s*=\s*\{(.*?)\};", src, re.S):
        full, size = m.group(1), int(m.group(3))
        data = bytes(int(h, 16) for h in HEX.findall(m.group(4)))
        if len(data) != size * size // 8:
            sys.exit(f"{full}: {len(data)} bytes, expected {size * size // 8}")
        # The firmware draws these with drawBitmap(..., WHITE, BLACK): a set bit
        # is white. PIL mode "1" also reads a set bit as white, so the bytes
        # load as-is and the PNG shows exactly what the panel shows.
        img = Image.frombytes("1", (size, size), data)
        path = ASSETS / "icons" / f"{full[len('icon_'):]}.png"
        img.save(path, optimize=True)
        print(f"{path.relative_to(SERVER)}")


def main() -> None:
    (ASSETS / "fonts").mkdir(parents=True, exist_ok=True)
    (ASSETS / "icons").mkdir(parents=True, exist_ok=True)
    for name in FONTS:
        import_font(name)
    import_icons()


if __name__ == "__main__":
    main()
