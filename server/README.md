# inkplate-screen

The server half of the dashboard: fetches weather and trains, runs the train
picker and the Buienradar vote, and draws the 800x600 1-bit frame the Inkplate
downloads. Design and decisions: [`../docs/server-rendering-design.md`](../docs/server-rendering-design.md).

Status: step 1 of that document (renderer on the laptop). The HTTP service,
telemetry and deploy come in steps 2 and 3.

## Layout

| Path | What |
|---|---|
| `screen/gfx.py` | 1-bit canvas that draws exactly like Adafruit GFX on the device |
| `screen/render.py` | The layout, ported section by section from `C_Display.ino` |
| `screen/sources.py` | The four APIs: fetch raw bodies, parse them as the firmware did |
| `screen/collect.py` | Per-source cache and refresh rules, produces a `Snapshot` |
| `screen/trains.py`, `weather.py`, `headline.py` | Picker, station vote, daily categories, greeting |
| `screen/preview.py` | Render on the laptop, record and replay fixtures |
| `screen/assets/` | Fonts and icons imported from the firmware (`tools/import_gfx_assets.py`) |
| `tests/` | Unit tests; `tests/fixtures/<name>/` holds recorded API responses |

Keep this folder free of `.h` and `.ino` files: CI builds a firmware release
for any pushed change to those.

## Running it

Python 3.12 or newer with Pillow (`pip install -r requirements.txt`).
Settings come from the environment; on the laptop put them in `local.env`
(gitignored), using `local.env.example` as the template. The NS key is needed
for trains; the rest works without it.

```sh
python -m screen.preview                     # live -> preview.png (2x) and frame.bin
python -m screen.preview --record NAME       # live, and save the responses as a fixture
python -m screen.preview --fixture NAME      # replay a fixture at its recorded time
python -m screen.preview --fixture NAME --at 2026-09-23T17:40 --battery 3.9
python -m pytest
```

On this laptop a globally installed pytest plugin (anyio with an old trio)
breaks collection; run the tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` set.

## Relation to the firmware

**The port matched the wall on 23 September 2026** (commit `c9a36d2`), down to
a firmware bug that left one train card in the evening. Drawing still follows
the firmware pixel for pixel: its own GFX fonts and bitmaps, Adafruit GFX's
integer algorithms, and single precision where the firmware's float rounding
decides pixels (small-caps spacing, the sun arc). Elsewhere double precision
can move a pixel in rare cases.

Changed on purpose since then:

1. **Trains compare full timestamps.** The firmware compared "HH:MM" text, so
   a train arriving after midnight (22:49, arriving 00:10) "beat" every
   earlier one, and from about 21:00 the wall showed a single card. Trains
   more than three hours ahead are left out (`trains.LOOKAHEAD`), so the
   evening shows the last trains rather than tomorrow's first. Delays are
   now exact differences of timestamps.
2. **`precipitation_hours` is read.** Open-Meteo sends it as a JSON float
   (`12.0`), and the firmware's `precipHours[i] | 0` returns the default for
   anything not stored as an integer (ArduinoJson 7.4.3), so the "3 or more
   hours of precipitation means drizzle" rule never fired.
3. **"Wet and windy" appears.** In the firmware, notable wind always set the
   "Windy" override first, and the combo needed no override to be set.
   Now a rainy, windy day reads "Wet and windy"; stormy, freezing, cold and
   hot still come first.
4. **Temperatures are rounded, not truncated.** The firmware cast to int:
   18.7 showed as 18, -0.6 as 0. Applies to the big temperature and the week
   strip, and to the feels-like value behind the headline.
5. **Structure.** Parsers turn JSON into typed records (`model.py`); the
   logic modules no longer see raw JSON or "HH:MM" strings. The unused
   `precipitation_probability_max` is no longer fetched.

Server-side by design (see the design document): per-source caching in
`collect.py` replaces the firmware's RTC caches, and the footer battery shows
the device's last reported voltage.
