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

## How exact the port is

Text, icons and shapes are drawn with the firmware's own GFX fonts and bitmaps
and with Adafruit GFX's integer algorithms, so the same input gives the same
pixels. The remaining differences:

- **Float rounding.** The firmware computes in single precision. The port
  matches it where it decides pixels systematically (small-caps letter
  spacing, the sun arc's sample steps) and uses double precision elsewhere,
  which can move a pixel in rare cases.
- **Caching.** The server's per-source cache and refresh rules
  (`collect.py`) replace the firmware's RTC caches, as the design document
  describes. Given the same responses, the frame is the same.
- **The footer battery** shows the device's last reported voltage.

## Firmware behaviour kept on purpose, to be decided

Found while porting. The port reproduces both so the preview can be checked
against the wall; each is a one-line change once the port is confirmed.

1. **`precipitation_hours` is always read as 0.** Open-Meteo sends it as a
   JSON float (`12.0`), and the firmware's `precipHours[i] | 0` returns the
   default for anything not stored as an integer (ArduinoJson 7.4.3). The
   "3 or more hours of precipitation means drizzle" rule in
   `calculateDailyWeather` has never fired. See `sources.parse_om_daily`.
2. **"Wet and windy" never appears.** Notable wind always sets the "Windy"
   override, and the combo requires that no override is set. See
   `headline.greeting`.
