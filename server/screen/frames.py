"""Frame formats on the wire. The contract is in docs/server-rendering-design.md.

A device lists what it can draw in `fmt=` (for example `fmt=g4z,m1z`); the
service picks one, names it in the `X-Format` header, and sends it:

    g4z   greyscale: 800x600 pixels at 4 bits, two per byte, left pixel in the
          high nibble, levels 0 (black) to 7 (white). Exactly the Inkplate
          library's 3-bit buffer (DMemory4Bit), 240,000 bytes, zlib-compressed.
    m1z   1-bit: 600 rows of 100 bytes, MSB first, 1 = black; 60,000 bytes,
          zlib-compressed.
    (none) a request without `fmt`: the same 1-bit frame, raw, no X-Format
          header. The contract's first form, kept so nothing that speaks it breaks.
"""

import zlib

from PIL import Image

GREY_BYTES = 240_000
MONO_BYTES = 60_000

# Preferred first, for each setting of SCREEN_FORMAT.
PREFERENCE = {"grey": ("g4z", "m1z"), "mono": ("m1z", "g4z")}


def pack_grey(img: Image.Image) -> bytes:
    """An 8-bit image already quantized to the 8 panel levels -> 240,000 bytes."""
    levels = img.convert("L").point(lambda v: round(v / 255 * 7)).tobytes()
    return bytes((a << 4) | b for a, b in zip(levels[0::2], levels[1::2]))


def pack_mono(img: Image.Image) -> bytes:
    """A mode "1" image -> 60,000 bytes. Pillow stores 1 = white; the wire says 1 = black."""
    return bytes(b ^ 0xFF for b in img.convert("1", dither=Image.Dither.NONE).tobytes())


def compress(frame: bytes) -> bytes:
    return zlib.compress(frame, 9)


def choose(offered: list[str], setting: str) -> str | None:
    """The format to send: the setting's preference among what the device
    offers; None means the raw 1-bit frame of a request without `fmt`."""
    for fmt in PREFERENCE.get(setting, PREFERENCE["grey"]):
        if fmt in offered:
            return fmt
    return None
