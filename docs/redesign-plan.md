# Inkplate Dashboard — Editorial Redesign + Trip Planner Switch

## Context

Two months of mockup iteration have produced a locked design at [Dashboard/design/mockup.html](Dashboard/design/mockup.html) — open in a browser to see the source of truth at native 800×600, 1-bit. The visual direction is **editorial / printed poster**: tracked small-caps section labels, dither textures, dynamic content, range bars instead of plain numbers.

Three behaviour changes go in alongside the visuals:

1. **Right weather panel rotates**: rain chart (when rain ≥0.1 mm in the next 3 h), otherwise a 24 h hourly temperature curve. Section label flips between **"Rain coming"** and **"Next hours dry"**.
2. **Per-card train station fallback**: if a Centraal train is cancelled or delayed ≥10 min, that one card swaps to an HS alternative (filled white-on-black pill makes it pop). Section header always reads `DEPARTURES · TO BREDA` — origin lives on the card.
3. **Transfer to Tilburg Universiteit on every card**: switch from the NS Departures endpoint to the NS **Trip Planner v3** endpoint, query DH→TBU and HS→TBU. Each card shows `→ Uni HH:MM` and warns when the Breda transfer is late/cancelled.

Plus the long-standing **rain-chart threshold-label off-panel bug** ([C_Display.ino:341-352](Dashboard/C_Display.ino#L341-L352)) gets fixed by drawing the labels inside the chart on small white plates.

## Coordinate system

The mockup uses 800×600 with origin top-left and is the exact specification. All coordinates below are pixel-accurate copies from the mockup SVG. Implementation must match.

## Typography — switch to Inter

The mockup uses **Inter** (system fallback in the browser). To make the device match the design instead of approximate it, we convert Inter to Adafruit GFX bitmap headers using the official `fontconvert` tool and check the headers into `Dashboard/Fonts/`. FreeSans is competent but generic; Inter was *designed for screens at small sizes* and carries the editorial character we've been chasing.

Plan: produce **7 headers** total (5 sizes × weights as needed):

- `Inter_Regular9pt7b.h`
- `Inter_Regular12pt7b.h`
- `Inter_Regular18pt7b.h` — for the train time at fs=28
- `Inter_Bold9pt7b.h` — small bold
- `Inter_Bold12pt7b.h` — track number, week max temp
- `Inter_Bold18pt7b.h` — greeting, if needed for stronger contrast
- `Inter_Bold48pt7b.h` — the hero temperature

(Naming convention is rop.nl's default and matches the internal `GFXfont` symbol in each file.)

Total flash **~350 KB measured** (was estimated 150–210 KB; the 48pt bold alone is 218 KB because all 95 ASCII chars are included — a future optimization would prune that one to digits only, dropping it to ~30 KB). The existing `FreeSans*` headers stay in the repo for now so a rollback is one `#include` change away.

If Inter conversion proves troublesome (size, license, or character set), **Atkinson Hyperlegible** is the runner-up — same conversion process, more distinctive letterforms, also free (OFL).

The mapping below is otherwise unchanged in structure — just `FreeSans*` → `Inter*` in every row.

**Sizing note** — Adafruit GFX point sizes don't map 1:1 to CSS pixels. Empirical mapping from the converted Inter headers:
- `Inter_*9pt7b`  → cap-height ≈ 13–14 px (line-height 21 px)
- `Inter_*12pt7b` → cap-height ≈ 17–18 px
- `Inter_*18pt7b` → cap-height ≈ 25–27 px
- `Inter_Bold48pt7b` → cap-height ≈ 64 px

Pick whichever lands closest to the mockup's CSS `font-size`. Treat the table as best-fit, not literal equivalence.

| Element                          | Mockup       | Device font                  | Notes |
|----------------------------------|--------------|------------------------------|-------|
| Big current temp ("18")          | fs=86 bold   | `Inter_Bold48pt7b` (exist) | Hero |
| Greeting ("Good morning")        | fs=22 bold   | `Inter_Bold18pt7b`              | Mockup `f-sans-b` = weight 700 |
| Train time ("14:23")             | fs=28 bold   | `Inter_Bold18pt7b`              | Mockup `f-sans-b` |
| Train track number               | fs=18 bold   | `Inter_Bold12pt7b` (NEW)   | Same baseline as time |
| Week day name                    | fs=13 bold   | `Inter_Bold9pt7b` (NEW)    | Centered |
| Week min temp label              | fs=11        | `Inter_Regular9pt7b` (exist)      | |
| Week max temp label              | fs=11 bold   | `Inter_Bold9pt7b` (NEW)    | |
| Temp-curve peak/trough labels    | fs=11 bold   | `Inter_Bold9pt7b` (NEW)    | |
| Transfer line — broken          | fs=11 bold   | `Inter_Bold9pt7b` (NEW)    | Pulls the eye |
| Date ("Sun · 24 May 2026")       | fs=14        | `Inter_Regular9pt7b`            | 9pt cap-height ≈ 14 px (12pt overshoots) |
| Wind ("14 km/h NE")              | fs=14        | `Inter_Regular12pt7b` (exist)     | |
| ~~Metadata ("Feels 16° · …")~~   | —            | dropped                         | Cluttered on device |
| Inline status ("on time", "+5m") | fs=12        | `Inter_Regular9pt7b` (exist)      | |
| Transfer line — ok               | fs=11        | `Inter_Regular9pt7b` (exist)      | |
| Disruption note ("Centraal …")   | fs=11        | `Inter_Regular9pt7b` (exist)      | |
| Chart axis / threshold labels    | fs=10–11     | `Inter_Regular9pt7b` (exist)      | |
| Section labels (small caps)      | fs=11 tracked| `Inter_Regular9pt7b` + helper     | Uppercase via helper, see below |
| Sunrise/sunset under arc         | fs=11        | `Inter_Regular9pt7b` (exist)      | |
| "DH HS" pill text (inverted)     | fs=11 sc     | `Inter_Regular9pt7b` + helper     | White on black |
| Footer "Updated HH:MM"           | fs=11        | `Inter_Regular9pt7b` (exist)      | |

**New font files to add to the sketch** (Adafruit GFX `Fonts/` folder): the 7 Inter headers listed above. The old `FreeSans*` headers have been removed from `Dashboard/Fonts/`; if rollback is ever needed, they can be re-copied from the Adafruit_GFX library install.

## Drawing primitives that don't map cleanly

Several mockup features have no direct Adafruit GFX equivalent and need small helpers. Put these at the top of `C_Display.ino` so the section renderers stay readable.

```cpp
// Draw uppercase text with manual letter-spacing, mimicking the SVG
// `letter-spacing: 0.22em` small-caps look. Per-char gap = 0.22 × charWidth
// plus a small constant; color param supports the inverted HS pill (WHITE).
void drawSmallCaps(int x, int y, const char* text, uint16_t color = BLACK, int extraGapPx = 1);
int  smallCapsWidth(const char* text, int extraGapPx = 1);

// Bayer 50% checker fill. Used for the rain chart fill and the disrupted-card
// no-longer-needed delay strip. Solid pure-black would be too heavy on e-ink.
void fillBayer50(int x, int y, int w, int h);

// Dashed line (single horizontal or vertical). Adafruit GFX has no dash support.
void drawDashedH(int x1, int x2, int y, int dashPx = 2, int gapPx = 3);
void drawDashedV(int x, int y1, int y2, int dashPx = 2, int gapPx = 3);

// Half-ellipse sunrise→sunset arc. Drawn pixel-by-pixel via parametric sin/cos.
void drawSunArc(int xLeft, int xRight, int baselineY, int rise);
// Returns the dot position on that arc for a given parametric t∈[0,1].
void sunDotForT(int xLeft, int xRight, int baselineY, int rise, float t,
                int& outX, int& outY);
```

All ~10–25 lines each. No new dependencies.

## Section-by-section layout

Every Y coordinate below is read directly from the locked mockup. Cross-check against `mockup.html` before changing anything.

### Masthead (y=0–92)

- Greeting: `Inter_Regular18pt7b` at (40, 48). Time-based:
  - hour < 12 → "Good morning"
  - hour < 18 → "Good afternoon"
  - else → "Good evening"
  - Night-mode (≥23 or <7): "Good evening" (matches the early-return cycle's last render).
- Date: `Inter_Regular12pt7b` at (40, 72). Format `"%s · %02d %s %Y"` e.g. `Sun · 24 May 2026`.
- Sun arc on the right:
  - Horizon: dashed line (40, _) from x=620 to x=760 at y=58 via `drawDashedH`.
  - Arc: half-ellipse via `drawSunArc(620, 760, 58, 24)`.
  - Sun dot: filled circle r=4 at the point returned by `sunDotForT(...,t)` where `t = (now − sunrise) / (sunset − sunrise)`, clamped to [0, 1].
  - Sunrise time: `Inter_Regular9pt7b` centered at (620, 78).
  - Sunset time: `Inter_Regular9pt7b` centered at (760, 78).
  - **Night branch** (`isNight == true`): mirror the day arc — same horizon, same `drawSunArc` geometry, but the parametric `t` is now `(now − sunset) / (24h − sunset + tomorrow_sunrise)` so the moon traverses from sunset (left, t=0) to sunrise (right, t=1) across midnight. Endpoint labels: left = today's sunset, right = tomorrow's sunrise (`forecast[1].sunrise`, or `forecast[0].sunrise` if forecast[1] missing). Dot is a mini crescent: filled black r=5 with an off-center white r=4 at `dotX − 2` (no phase variants — out of scope). Fallback when forecast is unavailable: original static crescent at (690, 50), r=14 black + r=12 white at (684, 50).
  - **`isNight` derivation**: in `updateDisplay`, compare current minute-of-day against today's sunrise/sunset from `forecast[0]`; fall back to a fixed 20h–06h window only when the forecast hasn't arrived. Same flag drives both the masthead and the weather-icon variant so they stay visually consistent.
- Thick rule: 2 px line from (40, 92) to (760, 92).

### Weather row (y=92–305)

- Section labels:
  - `drawSmallCaps(40, 112, "WEATHER")` — left
  - Right label is dynamic:
    - if `rainComing` → `drawSmallCaps(420, 112, "RAIN COMING")`
    - else → `drawSmallCaps(420, 112, "NEXT HOURS DRY")`

#### Left column (x=40–~400)

- 128×128 weather icon at (40, 125). Existing `drawWeatherIcon128` used as-is.
- Big temperature digits: `Inter_Bold48pt7b` at (195, 210) — baseline y=210.
- Degree symbol (filled outer ring r=9, inner r=4 white) at the cap-top of the digits. Cap-top approximated as `tempY − fontSize × 0.7` ≈ y=150 for the default font/size. Use `display.getTextBounds("18", 195, 210, …)` to find the right edge of the digits, then place the degree at `bbox.right + 8`, vertical at the computed cap-top.
- Wind row at y=232:
  - Rotated arrow: 22 px line at (197, 244) → (217, 244) rotated by the wind bearing around its midpoint. Triangular arrowhead at the tip (small filled triangle, 4 px tall).
  - Bearing text: `Inter_Regular12pt7b` at (227, 249) — format `"%d km/h %s"` where the cardinal compass is derived from the bearing in 8 buckets (N, NE, E, …).
- ~~Metadata line at y=272 `"Feels %d° · Humid %d%% · UV %d"`~~ — **dropped** (felt cluttered on device; the column reads cleaner with just icon + temp + wind).

#### Right column (x=420–750)

Show **one** of two charts based on `rainComing`. Both share y-range 140–260.

##### Wet — rain chart

- Axes: vertical line (420, 140)→(420, 260); horizontal (420, 260)→(750, 260).
- Area fill: build a path of 12 points across (i in 0..11): `x = 420 + i × 30`, `y = 260 − (mm/maxScale) × 120`. Fill the area between the path and the x-axis using `fillBayer50` for each thin vertical column (pixel-by-pixel inside the path). Stroke the top edge of the area with a solid line.
- Threshold reference lines (dashed horizontals via `drawDashedH`):
  - Heavy at y=180, Medium at y=215, Light at y=245 (computed from thresholds 2.5/1.0/0.1 over the chosen `maxScale`).
- **Bug fix**: threshold labels drawn **inside** the chart on small white plates:
  - "Heavy" — fillRect (710, 170, 40, 14) WHITE, then text right-aligned at (748, 181).
  - "Medium" — fillRect (700, 205, 50, 14) WHITE, then text at (748, 216).
  - "Light" — fillRect (713, 235, 37, 14) WHITE, then text at (748, 246).
- Hour ticks: walk the 12 intervals; where the wall-clock minute is 0, draw a 4 px tick below the x-axis and a hour label centered (`HH:00`) at y=276.

##### Dry — 24h temperature curve

- Axes: same vertical and horizontal lines as the rain chart so the section's structure feels consistent.
- Vertical day/night guides: at the hour offset of today's sunset and tomorrow's sunrise within the 24 h window, draw a dashed vertical via `drawDashedV` from y=136 to y=260. Label "sunset" / "sunrise" at y=132 centered on the guide.
- Curve: 24 hourly points sampled from `hourlyTemp[24]`. Linearly map temps to y between (yMin, yMax) padded ±1 °C. Draw straight segments between consecutive points (no smoothing — keeps the line crisp on e-ink).
- Min / max markers: filled circles r=3 at the min and max points. Labels:
  - Max temp: `Inter_Bold9pt7b`, centered, baseline 8 px above the dot.
  - Min temp: `Inter_Bold9pt7b`, centered, baseline 16 px below the dot.
- X-axis tick labels at "now / +6h / +12h / +18h" via `Inter_Regular9pt7b` at y=276.

### Week strip (y=305–445)

- Dotted divider at y=305 via `drawDashedH`.
- Section label at y=324: `drawSmallCaps(40, 324, "WEEK")`.
- 7 cells × 102 px wide, starting at x=40. Each cell centered around its midpoint (`cMid = cellX + 51`):
  - Day name: `Inter_Bold9pt7b` centered at (cMid, 340).
  - 48×48 weather icon centered at (cMid − 24, 350).
  - Range bar at y=410 within the cell. Bar runs from `cMid − 40` to `cMid + 40` (80 px). Background: dashed thin line (`drawDashedH`). Filled segment between the day's min and max temps, computed against the **shared scale [-2°, +25°]**. Filled dots r=3 at each end. Labels:
    - Max temp: `Inter_Bold9pt7b` centered above the right dot at y=404.
    - Min temp: `Inter_Regular9pt7b` centered below the left dot at y=424.

### Departures (y=455–590)

- Dotted divider at y=455 via `drawDashedH`.
- Section label at y=474: `drawSmallCaps(40, 474, "DEPARTURES · TO BREDA")`. **No station mentioned at the section level** — origin lives in each card.
- Three boxed cards starting at x=40, each 220 wide, 80 tall, with 20 px gap between cards (so card N is at x = 40 + N × 240, y=485). For each card `c` indexed by slot `i = 0..2`:

  - **Outline**: `drawRect` with 1.25 px stroke (so on 1-bit, two adjacent pixel-wide lines or single 2 px stroke). On HS cards, use a 2 px outline.
  - **Origin** at the top-left:
    - **Centraal**: `drawSmallCaps(cardX + 14, cardY + 18, "DH CENTRAAL")`.
    - **HS**: filled black `fillRect(cardX + 4, cardY + 4, 64, 18)`, then white text `drawSmallCaps(cardX + 36, cardY + 18, "DH HS", color=WHITE)` centered in the pill. Next to the pill at `cardX + 74, cardY + 18`, the **disruption note** in `Inter_Regular9pt7b`, e.g. `"Centraal cancelled"` or `"Centraal +15m"`.
  - **Time** at (cardX + 14, cardY + 48): `Inter_Regular18pt7b`.
  - **Inline status** to the right of the time at (cardX + 106, cardY + 46): `Inter_Regular9pt7b` — e.g. `"on time"`, `"+5m late"`, `"cancelled"`. Omit when the card has no status to display (e.g., when the card is HS-alt and the HS train itself is on time but we want to keep the note line clean — actually still show the HS train's status here; the *Centraal* context is in the pill-adjacent note).
  - **Track number** at the right: `Inter_Bold12pt7b` right-aligned at (cardX + 206, cardY + 46). No "Track" label — the number is enough.
  - **Transfer line** at (cardX + 14, cardY + 72): "→ Uni HH:MM" in `Inter_Regular9pt7b`. When the Breda→Tilburg leg is late: append `" · transfer late"` and switch to `Inter_Bold9pt7b`. When that leg is cancelled: render `"→ Uni · transfer cancelled"` in bold.

### Footer (y=580–600)

- Updated time at (40, 590): `Inter_Regular9pt7b` — `"Updated %02d:%02d"`.
- Battery icon at the right: existing implementation in `drawFooter` is correct; no changes.

## Per-card train picker (replaces "fetch HS only when Centraal fails")

We always call **NS Trip Planner v3** twice (one per origin: GVC and GV), each returning up to 6 trips to TBU. Then walk the merged Centraal trips by time and apply the per-slot rule:

```text
for i in 0..2:
  ctr = next un-picked Centraal trip after the previous slot's chosen departure time
  if ctr.firstLeg is on-time-or-<10m-delay AND not cancelled:
    slot[i] = ctr  (origin = CENTRAAL)
  else:
    hs = next un-picked HS trip where:
        not cancelled,
        scheduledDeparture within ±20 min of ctr.scheduledDeparture,
        actualDeparture ≥ now + 5 min   (so the user can actually get to HS)
    if hs found:
      slot[i] = hs  (origin = HS, note = brief Centraal disruption summary)
    else:
      slot[i] = ctr  (still bad, but no better option — show as cancelled/delayed)
```

For each chosen trip, we extract for the card:
- `time` = `legs[0].origin.actualDateTime` (so a +5m delayed Centraal shows the *actual* departure). **Fallback to `plannedDateTime` when `actualDateTime` is empty** — the spike showed that future-leg arrivals have empty `actualDateTime` until realtime data exists; that does NOT mean cancelled.
- `track` = `legs[0].origin.plannedTrack` (NS rarely changes track late; field present per spike).
- `status` = derived from `legs[0]`: `"cancelled"` (cancelled || partCancelled), `"+%dm late"` (actual − planned ≥ 1 min), or `"on time"`.
- `uniArr` = `legs[N-1].destination.actualDateTime` (fallback to `plannedDateTime` when empty).
- `transfer` — note: spike showed every DH→TBU trip has exactly **2 legs** (IC + SPR via Breda); there is no walk-leg. Derived from `legs[N-1]` (the SPR Breda→TBU):
  - If `cancelled || partCancelled` → "cancelled".
  - Else if last-leg `origin.actual` − `origin.planned` ≥ 5 min → "late".
  - Else → "ok". (The single-leg branch is dead code in practice; keep it as defensive fallback for trips that happen to start at Breda or terminate without transfer.)
- `note` (HS-only, when an HS trip replaces a Centraal slot): from the Centraal trip we *would* have used:
  - cancelled → `"Centraal cancelled"`
  - delayed → `"Centraal +%dm"`

**Useful bonus fields surfaced by the spike** (not strictly required, but worth knowing about): `legs[].direction` (e.g. "Eindhoven Centraal"), `legs[].punctuality`, `legs[].transferTimeToNextLeg` (transfer slack in minutes — could be used as an extra "tight transfer" warning later), `legs[].crossPlatformTransfer` (boolean).

**Picker note — dedup duplicate trips**. Step 3 device spike confirmed that NS Trip Planner returns multiple "routing options" for the same physical train (different via-station preferences hash to different `uid`s but produce identical `plannedDepartureISO`). Before applying the per-slot rule above, walk the candidate list and skip any trip whose `plannedDepartureISO` matches the previously kept one. Without dedup, slots 1 and 2 would be the same train.

## Files to modify

- [Dashboard/Dashboard.ino](Dashboard/Dashboard.ino)
  - Extend `Train` struct (or replace with `Departure` carrying the extra fields): `origin` (enum CTR/HS), `note` (char[24]), `uniArr` (char[6]), `transferStatus` (enum OK/LATE/CANCELLED).
  - Add a `WeatherExtras` struct: `windDirection`, plus a `hourlyTemp[24]` array. (Originally also held `apparentTemp`/`humidity`/`uvIndex` for the metadata line — removed when the line was dropped.)
  - Always call `fetchTrips(STATION_CODE_CENTRAL, …)` and `fetchTrips(STATION_CODE_HS, …)` (no conditional skipping any more).
  - Run the per-slot picker (new helper in `A_Calculations.ino`), produce 3 `Departure`s, pass them to `drawTrains`.
- [Dashboard/B_Network.ino](Dashboard/B_Network.ino)
  - **Replace** `getTrains(...)` (which hits the Departures API) with `fetchTrips(fromStation, toStation, dest[], maxCount)` hitting `https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips?fromStation=…&toStation=TBU`. Same `Ocp-Apim-Subscription-Key` header, same retry/timeout pattern as today.
  - Parse the `trips[]` array: per trip pull `legs[0]` (origin leg) and `legs[N-1]` (final leg) plannedDateTime / actualDateTime / cancelled / plannedTrack.
  - `fetchOpenMeteo`: add to the query string:
    - `current=…,apparent_temperature,relative_humidity_2m,uv_index,wind_direction_10m`
    - `hourly=temperature_2m` (already requested for forecast? No — currently we only ask for `daily=...`. Add a fresh `hourly=temperature_2m` and consume the **first 24** entries starting from the current hour).
- [Dashboard/A_Calculations.ino](Dashboard/A_Calculations.ino)
  - Add `pickDepartures(Departure ctr[], int nCtr, Departure hs[], int nHs, Departure out[3])` implementing the per-slot rule above.
  - Add `cardinalCompass(int bearingDeg)` returning one of the 8 strings ("N", "NE", …).
- [Dashboard/C_Display.ino](Dashboard/C_Display.ino)
  - Add the 4 helpers (`drawSmallCaps`, `fillBayer50`, `drawDashedH/V`, `drawSunArc` + `sunDotForT`).
  - Add `#include` for the 7 new Inter headers: `Fonts/Inter_Regular9pt7b.h`, `Fonts/Inter_Regular12pt7b.h`, `Fonts/Inter_Regular18pt7b.h`, `Fonts/Inter_Bold9pt7b.h`, `Fonts/Inter_Bold12pt7b.h`, `Fonts/Inter_Bold18pt7b.h`, `Fonts/Inter_Bold48pt7b.h`. Remove the old `FreeSans*` includes once parity is verified (keep the header files in the repo for rollback).
  - Rewrite `drawHeader` per Masthead spec; gain a `bool isNight` parameter.
  - Rewrite `drawCurrentWeather(temp, code, wind, bearing, isNight)` per Left column spec (metadata-line params removed with the line itself).
  - Rewrite `drawRainChart(rain[], times[], n)` per Wet spec including the threshold-label-inside fix.
  - New `drawTempCurve(hourlyTemp[24], nowHour, sunriseH, sunsetH)` per Dry spec.
  - In `updateDisplay`, branch on `rainComing` to call rain vs. temp curve, and update the right-panel section label accordingly.
  - Rewrite `drawWeekForecast(forecast[], n)` per Week strip spec (centered cells, range bars).
  - Rewrite `drawTrains(Departure d[3])` per Departures spec (HS pill, inline status, transfer line, etc.).
- [Dashboard/icons.h](Dashboard/icons.h)
  - Add 48×48 forecast variants of every existing 64×64 icon. Initial bitmaps produced by a one-off Python downscale script (see "Icons" section below).
  - Delete `icon_train_32` and `icon_raindrop_16` (no longer drawn).
  - **Moon glyph**: `icon_moon_128` already exists in `icons.h`. Reuse it for the masthead night branch (downscale at draw time, or hand-author a smaller variant only if quality demands it). No new asset required.
- [Dashboard/config.h.example](Dashboard/config.h.example)
  - Document the destination station code constant: `#define STATION_CODE_DESTINATION "TBU"` and the label used on the transfer line: `#define DESTINATION_SHORT_LABEL "Uni"`. Default to TBU / "Uni". (Reeshof variant out of scope.)

## Icons

A one-off Python helper at `Dashboard/design/downscale_icons.py` reads the existing 64×64 1-bit byte arrays from `icons.h`, nearest-neighbor downscales them to 48×48, and emits new `_48` variants in the same `PROGMEM` byte-array format. The script is committed but only re-run manually when source icons change.

Expected result: usable but soft 48×48 glyphs. If a particular glyph (e.g. `icon_lightning_64`) downscales poorly, you can replace just that one with a hand-redrawn 48×48 by editing `icons.h` directly. The structure of the file stays identical so the rest of the build is unaffected.

## Bug fix — rain-chart threshold labels

Solved by the "inside on white plates" approach in the rain chart spec above. Concretely: replace the current `drawDashedLine` lambda in [C_Display.ino:341-352](Dashboard/C_Display.ino#L341-L352) with code that draws the dashed line, then a small `fillRect(..., WHITE)` plate at the right end inside the chart bounds, then the label text right-anchored inside that plate.

## Risks and mitigations

These are the soft spots I want explicit in the plan so they don't surprise us mid-implementation:

1. **NS Trip Planner subscription scope.** ~~Your current API key was registered against Reisinformatie v2 (Departures). Trip Planner v3 may require a separate subscription on the NS API portal.~~ **RESOLVED in Step 1 spike (2026-05-24)**: existing key returns HTTP 200 against `…/api/v3/trips`. No portal change needed.
2. **Trip Planner payload size.** ~~Per call ~30–50 KB~~ **Spike measured 86 KB (GVC→TBU) and 94 KB (GV→TBU)**. **RESOLVED in Step 3 device spike (2026-05-24)**: streaming-with-Filter against `http.getStream()` works reliably on hardware — no `IncompleteInput` errors. Per-call transient heap cost is ~50 KB (TLS context dominates), **fully released between calls** (153 KB free → 101 KB during fetch → 153 KB free again). Plenty of headroom on the ~200 KB post-WiFi heap budget. Filter definition lives at the top of `fetchTrips` in `B_Network.ino`.
3. **48×48 icon quality from downscale.** Nearest-neighbor 64→48 is ratio 0.75 — pixel-misaligned. Detailed glyphs (lightning, snowflake) will chew up. **Mitigation**: plan that the script is a starting point; hand-redraw the worst offenders. Don't ship if the icons look bad.
4. **Bayer fill performance.** ~~Pixel-by-pixel calls across 40 k pixels is ~1.5 s overhead.~~ **Step 4 resolution**: implemented as stride-of-2 single-pixel checker (`fillBayer50`) — Inkplate's `drawPixel` writes straight to the frame buffer (no display refresh per pixel), so even the naive form is fast. The "drawLine for 2-pixel segments" idea doesn't apply to a fine 50% checker (no 2-pixel runs exist in the pattern); will re-evaluate if the actual rain-chart fill in Step 8 measurably slows the wake cycle.
5. **Sun arc rendering.** ~~Parametric pixel sampling can leave gaps on steep parts of the arc.~~ **Step 4 resolution**: went with parametric sampling at 0.005 rad steps (~630 samples for an rx≈70 / ry=24 arc). At those dimensions parametric is gap-free, and `sunDotForT` reuses the same math trivially. If a future larger arc shows aliasing, fall back to `display.drawEllipse` clipped to the top half.
6. **HS station code in Trip Planner v3.** ~~Reisinformatie v2 used `GV` for Den Haag HS. Trip Planner may want `GVH` or the longer code.~~ **RESOLVED in Step 1 spike**: `fromStation=GV` → 200 OK with trips originating "Den Haag HS"; `fromStation=GVH` → HTTP 400. Keep `STATION_CODE_HS = "GV"`.
7. **ISO 8601 timezone parsing.** Trip Planner actually returns offsets *without* a colon, e.g. `2026-05-24T14:23:00+0200` (not `+02:00`). `strptime` `%z` handles both forms on glibc/newlib. Comparing with local time across DST still needs care. **Mitigation**: parse with `strptime` (POSIX) + apply the parsed offset, then convert to `time_t` and compare in epoch seconds (DST-safe).
8. **Letter-spacing visual quality.** A fixed pixel gap looks uneven across narrow vs wide chars. **Mitigation**: gap = `round(0.2 × charWidth)` per char, measured via `getTextBounds`.
9. **Both APIs fail at once.** Currently we'd render half a dashboard. **Mitigation**: Step 13 of the implementation order adds explicit "data unavailable" fallback states for both trains and the dry-state temp curve.
10. **Flash and heap budget.** New fonts ~200 KB flash (fine, 4 MB partition). Heap: Trip Planner JSON parse with filter ~6 KB doc, plus 24 hourly temps + 12 departures + week forecast ~3 KB structs. Comfortable. **Mitigation**: monitor `ESP.getFreeHeap()` in serial log after each parse.

If any of these blocks at implementation time, drop back to the plan file and document the workaround there rather than diverging silently.

## Refresh strategy

`FULL_REFRESH_EVERY` is raised from 4 to **12** (every 3 hours instead of hourly). The full layout still repaints every wake; we don't do region-targeted partials. With Bayer fill and dynamic content, partial refresh can ghost, but every 3 h of catch-up is fine for an e-ink display read at glance distance.

## Battery and runtime cost

Each new feature has a runtime cost. Without mitigation, total daily energy goes from ~45 mAh today to ~50 mAh after this redesign. The optimizations below claw it back to **~25 mAh/day → roughly 35–45 days realistic battery life on the stock Inkplate cell**, roughly doubling current life while shipping the heavier dashboard.

### Optimizations to implement (in this order)

1. **RTC RAM caches for stable data.** `RTC_DATA_ATTR` is already in use for `wakeCounter` ([Dashboard.ino:79](Dashboard/Dashboard.ino#L79)), so the pattern is proven on this board. (CLAUDE.md's "no persistent RAM between cycles" line should be tightened to "no heap persistence; RTC RAM persists" during Step 14 cleanup.) Add `RTC_DATA_ATTR` storage for:
   - Last full week forecast + the date it was fetched. Only refetch when the date rolls over (first wake after midnight that has working internet).
   - Last `Departure[3]` array. Used as a fallback when a trip planner fetch fails — render the last-known-good board instead of "unavailable".
   - A 32-bit hash of the data fields that drive the rendered frame. Compute it before `display.display()` — if identical to the last cycle's hash, skip the display refresh entirely (saves ~1.5–2 s of active time per identical cycle).
2. **Static IP.** Add `WiFi.config(STATIC_IP, GATEWAY, SUBNET, DNS)` in `initHardware()` before `WiFi.begin()`. New constants go in `config.h.example`. Saves ~0.5–1.5 s/wake of DHCP.
3. **Last-known-good train fallback.** Combined with the RTC cache above: when both `fetchTrips` calls fail, render the previous cycle's `Departure[3]` and dim the section label (or append "· stale") so the user knows the data is from before. Better than the empty-state described in Step 13.
4. **Daily-only forecast fetch.** Open-Meteo daily forecast = once per day. Hourly + current = every cycle (those *do* change quickly).

Note: `SLEEP_DURATION` **stays at 900 s (15 min)** all day — rain and train conditions change quickly enough that the lower freshness of an adaptive schedule isn't worth the modest battery saving. The RTC caches (#1) and last-known-good fallback (#3) carry most of the win without sacrificing freshness.

### Optimizations consciously skipped

- TLS cert pinning: small power win, big code/maintenance cost. Defer until the device stops working over plain `setInsecure()` (which it isn't).
- Switching MCU clock further: `setCpuFrequencyMhz(80)` is already in place. Lower drops cause WiFi instability.
- Region-targeted partial refresh: ghosting risk with dithered fills is too high.
- Sleep current optimization: ~30–40 µA is already near the floor for Inkplate 6's onboard regulators.

### Verification for battery work

- Serial-log the wake-active duration each cycle (`millis()` at `setup()` start vs at `goToSleep()`). Confirm caches drop it from ~17 s to ~12 s.
- Serial-log `ESP.getFreeHeap()` before/after each fetch + parse. Confirm Trip Planner filtered parse stays well under 30 KB heap delta.
- Run the device for 7 days on a known starting charge; measure end-of-week voltage drop. Project to days-until-3.5V.


## Implementation order

Reordered so the **risky external dependencies are validated first** — Trip Planner API access and Inter font conversion. UI work happens against confirmed data.

1. **Spike: NS Trip Planner access** — ✅ **DONE 2026-05-24**. Both `fromStation=GVC&toStation=TBU` and `fromStation=GV&toStation=TBU` returned HTTP 200 with the existing API key. Sample payloads saved at `Dashboard/design/trip_gvc_to_tbu.json` (86 KB) and `Dashboard/design/trip_gv_to_tbu.json` (94 KB). Helper scripts: `Dashboard/design/trip_spike.ps1`, `Dashboard/design/inspect_trip.ps1`. Field structure verified against `legs[].origin.{plannedDateTime,actualDateTime,plannedTrack,actualTrack}` and `legs[].{cancelled,partCancelled}`. **Picker note**: all DH→TBU trips have exactly 2 legs (IC + SPR), no walk-leg.
2. **Inter font conversion** — run Adafruit's `fontconvert` against Inter TTF for the 7 size/weight variants. Drop headers into `Dashboard/Fonts/`. Compile-only test that they include cleanly.
3. **Trip Planner network + struct** — rewrite `fetchTrips` to hit v3 with `ArduinoJson` `DeserializationOption::Filter` against `http.getStream()` (streaming, not slurp — see risk #2). Keep only `trips[].legs[].{cancelled,partCancelled,origin.plannedDateTime,origin.actualDateTime,origin.plannedTrack,destination.plannedDateTime,destination.actualDateTime}`. Define `Departure` struct. Test with serial-log dump from the device against real disruption + non-disruption windows. Sample payloads at `Dashboard/design/trip_*.json` can be used for offline parse testing.
4. **Drawing helpers** — ✅ **DONE 2026-05-24**. `drawSmallCaps` + `smallCapsWidth` (proportional 0.22×charWidth gap, color param for the HS pill), `fillBayer50` (stride-of-2 single-pixel checker — note: deviated from plan's "drawLine 2-pixel segments" which doesn't apply to a fine checker), `drawDashedH` / `drawDashedV`, `drawSunArc` + `sunDotForT` (parametric sampling at 0.005 rad — deviated from plan's `drawEllipse`-clipped suggestion; parametric gives free `sunDotForT` and is gap-free at the arc dimensions we need). Pure additions in `C_Display.ino`; no visual change yet.
5. **Masthead** — rewrite `drawHeader`. Flash, verify greeting + sun arc + moon.
6. **Weather row left** — rewrite `drawCurrentWeather`. Flash, verify.
7. **Temp curve + dynamic right label** — add `drawTempCurve` + `rainComing` switch. Flash, verify both states with hardcoded data.
8. **Rain chart fix + Bayer fill** — apply the threshold-label-inside fix and dither fill. Flash, verify.
9. **Week strip with 64×64 icons** — rewrite `drawWeekForecast` with range bars. Use existing icons at `cMid−32, 350` initially to keep the step focused on layout.
10. **48×48 icons** — run the downscale script, drop in the new icons, tighten cell to `cMid−24, 350`. **Hand-redraw any glyph that downscales badly** (lightning and snowflake are likely candidates).
11. **Train cards** — rewrite `drawTrains` for the new card layout (HS pill, inline status, transfer line). Use real `Departure[]` data from step 3.
12. **Per-slot picker** — add `pickDepartures` in `A_Calculations.ino`. Force-test by mocking one cancelled / one delayed Centraal trip.
13. **Empty / failure states** — when both trip-planner calls fail or return empty: skip the cards and render "Train data unavailable" at (40, 510) in `Inter_Regular12pt7b`. When `hourlyTemp[]` is missing in the dry state: render "Outlook unavailable" centered in the right panel.
14. **Cleanup** — remove old `Train` struct, delete `icon_train_32` / `icon_raindrop_16`, update CLAUDE.md to mention the design mockup, the Inter fonts, and the Trip Planner endpoint. Bump the version line in `Dashboard.ino` boot log.

## Verification

For each step's "flash, verify" above, **and especially after step 11**:

- **Mockup parity check**: open `mockup.html` next to the device. Toggle through the 4 scenarios (Normal / Rain hero / Train disruption / Night) and visually diff against the device. Typography will differ (FreeSans vs Inter) but layout, hierarchy, and information density should match.
- **Force-trigger states** by temporarily hardcoding in `setup()` after the real fetches:
  - Set `rainData[5] = 1.5` → rain chart appears, label = "Rain coming".
  - Zero out `rainData[*]` → temp curve, label = "Next hours dry".
  - Mark one Centraal trip cancelled and one delayed +15 m → slots 2 & 3 become HS-pill cards with notes; slot 1 stays Centraal; section header stays "TO BREDA".
  - Set `timeinfo.tm_hour = 0` (faked) → masthead shows moon glyph + "Good evening", sun arc hidden.
- **No off-panel drawing**: rip the SD-card output (if SD is configured) or visually scan the panel edges. Nothing should print past x=800 or below y=600.
- **Regression**: a normal cycle (no rain, all on-time Centraal, daytime) must produce the "Normal" mockup view exactly — no disruption indicators, no rain chart, temp curve visible.

## Out of scope for now

- Calendar / agenda integration.
- Custom illustration / hero artwork beyond icons + dither textures.
- Severe-weather banner.
- Moon-phase variants per-day.
- Reeshof as an alternative final destination.
- Air quality / pollen / sea-level / additional data sources.
- "Imminent train (<5 min) grows" and "wind/UV verdict line" rotations (discussed, not adopted).
