# inkplate-screen

The server half of the dashboard: fetches weather and trains, runs the train
picker and the Buienradar vote, and draws the 800x600 1-bit frame the Inkplate
downloads. Design and decisions: [`../docs/server-rendering-design.md`](../docs/server-rendering-design.md).

Status: steps 1 and 2 of that document are done (renderer and service, on
the laptop). Steps 3 and 4 are done: CT 106 has served the screen and deployed itself
since 24 September 2026, and Home Assistant takes the device's reports. Step 5,
the thin firmware, was released the same evening (`v2026.09.24-01`).

## Layout

| Path | What |
|---|---|
| `screen/render.py` | **The design**: the layout, drawn for the greyscale and the 1-bit panel mode |
| `screen/frames.py` | The wire formats (`g4z` greyscale, `m1z` 1-bit, both zlib) and which one a device gets |
| `screen/sources.py` | The four APIs: fetch raw bodies, parse them as the firmware did |
| `screen/collect.py` | Per-source cache and refresh rules, produces a `Snapshot` |
| `screen/trains.py`, `weather.py`, `headline.py` | Picker, station vote, daily categories, greeting |
| `screen/preview.py` | Render on the laptop, record and replay fixtures |
| `screen/service.py` | The HTTP service: render loop, `/v1/screen`, `/preview.png`, `/status` |
| `screen/schedule.py` | When the device wakes next, OTA hint, 200 or 204, full or partial refresh |
| `screen/telemetry.py` | The device's report: parsing, `state.json`, forwarding to HA and healthchecks.io |
| `screen/assets/ttf/` | Inter (OFL, variable) and the Material Symbols weather subset (Apache 2.0), with their licences |
| `tools/subset_icons.py` | Rebuilds the icon subset from Google's font, byte for byte |
| `tools/mockups.py` | The screen in its three looks side by side, from a fixture and a synthetic rainy morning |
| `screen/selftest.py` | The gate a deploy must pass: renders the fixtures, checks the frame and schedule |
| `deploy/` | CT 106: `deploy.sh` and the systemd units (installed by hand, see below) |
| `tests/` | Unit tests; `tests/fixtures/<name>/` holds recorded API responses. Only `wall1` is tracked (the tests and the self-test use it); new recordings stay local unless given a `!` line in `.gitignore` |

Keep this folder free of `.h` and `.ino` files: CI builds a firmware release
for any pushed change to those.

## Running it

Python 3.12 or newer with Pillow: `pip install pillow==11.3.0` on the laptop.
`requirements.txt` pins the Linux wheels by hash for the server, so pip
refuses it on Windows.
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

The service (see its docstring for the endpoints):

```sh
BIND=127.0.0.1 python -m screen.service                  # live APIs on port 8088
BIND=127.0.0.1 python -m screen.service --fixture wall1 --at 2026-09-24T00:05:40
curl -D - -o frame.bin "http://127.0.0.1:8088/v1/screen?batt=3.91&fw=dev&wake=1"
```

Open `http://127.0.0.1:8088/preview.png` in a browser to see the current frame.

On this laptop a globally installed pytest plugin (anyio with an old trio)
breaks collection; run the tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` set.

## Relation to the firmware

**The port matched the wall on 23 September 2026** (commit `c9a36d2`), down to
a firmware bug that left one train card in the evening: it drew with the
firmware's own GFX fonts and bitmaps and Adafruit GFX's integer algorithms.
That exact port did its job and was removed on 24 September 2026, when the
redesign became the only renderer; it is in git history if ever needed.

Changed on purpose since then, besides the redesign:

1. **Trains compare full timestamps.** The firmware compared "HH:MM" text, so
   a train arriving after midnight (22:49, arriving 00:10) "beat" every
   earlier one, and from about 21:00 the wall showed a single card. Trains
   more than three hours ahead are left out (`trains.LOOKAHEAD`), so the
   evening shows the last trains rather than tomorrow's first. Delays are
   now exact differences of timestamps.
2. **`precipitation_hours` is read.** Open-Meteo sends it as a JSON float
   (`12.0`), and the firmware's `precipHours[i] | 0` returns the default for
   anything not stored as an integer (ArduinoJson 7.4.3), so the "3 or more
   hours of precipitation means drizzle" rule never fired. (Since 25 September
   2026 the week's icons come from the hourly values instead: item 6.)
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
6. **The week's icons judge the daytime.** Open-Meteo's daily code is the
   worst hour of 24, nights included; each day is now judged on its hourly
   values for 07:00-21:00, with a sun-and-showers icon for a few wet hours
   on a bright day. Rules and the data behind them:
   [`../docs/weather-categories.md`](../docs/weather-categories.md).

Server-side by design (see the design document): per-source caching in
`collect.py` replaces the firmware's RTC caches, and the footer battery shows
the device's last reported voltage.

## On the server

CT 106 runs the service as user `screen` from `/opt/inkplate-screen/current`,
and deploys itself: every 5 minutes `deploy.sh` (as `screen-deploy`) fetches
`master`, and when `server/` changed it builds a release with its own venv,
installs from hashes, runs `python -m screen.selftest`, and only then switches
the `current` symlink; a path unit restarts the service. So **a push to master
that touches `server/` is live within about 5 minutes**, and one that fails
the self-test never goes live. The firmware's CI ignores `server/`.

`deploy.sh` and the unit files are copied into the container by hand, so a push
cannot change how deploys work. After editing them here, copy them again.
The build and operation of CT 106 are in the home-server repo,
`runbooks/inkplate-screen.md`.
