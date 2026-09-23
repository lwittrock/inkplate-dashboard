"""Rebuild screen/assets/ttf/MaterialSymbolsRounded-weather.ttf.

    pip install fonttools
    python tools/subset_icons.py

Downloads Material Symbols Rounded (Apache 2.0, ~15 MB) from Google's
repository, pins the fill and grade axes at 0, keeps only the weather glyphs
screen/render.py draws, and writes the subset (~16 KB) with its codepoints
file and licence. Weight and optical size stay variable. Run it after adding
a glyph to GLYPHS (and to render.material_name).
"""

import tempfile
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

BASE = "https://github.com/google/material-design-icons/raw/master/variablefont/"
NAME = "MaterialSymbolsRounded%5BFILL,GRAD,opsz,wght%5D"
LICENSE = "https://raw.githubusercontent.com/google/material-design-icons/master/LICENSE"
OUT = Path(__file__).resolve().parent.parent / "screen" / "assets" / "ttf"

GLYPHS = ["sunny", "clear_night", "partly_cloudy_day", "partly_cloudy_night", "cloud", "foggy",
          "rainy", "weather_snowy", "sunny_snowing", "thunderstorm"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ttf = Path(tmp) / "full.ttf"
        ttf.write_bytes(urllib.request.urlopen(BASE + NAME + ".ttf", timeout=300).read())
        codepoints = dict(line.split() for line in
                          urllib.request.urlopen(BASE + NAME + ".codepoints", timeout=60).read()
                          .decode().splitlines())
        font = instancer.instantiateVariableFont(TTFont(ttf), {"FILL": 0, "GRAD": 0})
        opts = subset.Options()
        opts.layout_features = []
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=[int(codepoints[g], 16) for g in GLYPHS])
        sub.subset(font)
        font.recalcTimestamp = False     # keep Google's timestamp: the same input gives the same bytes
        font.save(OUT / "MaterialSymbolsRounded-weather.ttf")
    (OUT / "MaterialSymbolsRounded-weather.codepoints").write_text(
        "".join(f"{g} {codepoints[g]}\n" for g in GLYPHS), encoding="utf-8", newline="\n")
    (OUT / "LICENSE-MaterialSymbols.txt").write_bytes(urllib.request.urlopen(LICENSE, timeout=60).read())
    print(f"{OUT / 'MaterialSymbolsRounded-weather.ttf'}: {len(GLYPHS)} glyphs")


if __name__ == "__main__":
    main()
