# Inkplate 6 Dashboard — CLAUDE.md

## Project Overview

E-paper weather + train dashboard running on an **Inkplate 6** (ESP32-based, 800×600px 1-bit e-ink display). The device wakes every 15 minutes, fetches live data, renders a full-screen editorial layout, then deep-sleeps until the next cycle. Night mode (23:00–07:00) skips data fetching and sleeps through the night.

**Location:** Delft, Netherlands
**Origin stations:** Den Haag Centraal (GVC) and Den Haag HS (GV)
**Destination:** Tilburg Universiteit (TBU) — both origins queried via NS Trip Planner v3, a per-slot picker substitutes HS trips when a Centraal slot is disrupted.

**Design source of truth:** the rendered display itself, plus the absolute Y coordinates and section comments in [C_Display.ino](C_Display.ino). An earlier SVG mockup (`design/mockup.html`) seeded the layout but is no longer maintained.

---

## Architecture

The sketch is split into four `.ino` files (all compiled together by Arduino IDE):

```
Dashboard.ino        — Entry point: data structures, setup(), loop()
A_Calculations.ino   — Weather categorisation, cardinal compass, ISO 8601 parse,
                       pickDepartures (per-slot CTR/HS picker)
B_Network.ino        — WiFi, NTP, Open-Meteo, NS Trip Planner v3
C_Display.ino        — All rendering: fonts, icons, drawing helpers, sections
```

**Data flow:**
```
setup()
  ├─ initHardware()           WiFi connect + NTP sync (POSIX TZ)
  ├─ handleNightMode()        deep sleep if 23:00–07:00
  ├─ fetchOpenMeteo()         24h hourly temps + 7-day daily forecast
  ├─ fetchBuienradarNow()     live KNMI station: temp, weather code, wind
  ├─ fetchBuienradarRain()    2h rain nowcast (24 × 5-min mm/h samples)
  │     ↑ on failure: restore from RTC_DATA_ATTR brCache (nowValid/rainValid)
  ├─ fetchTrips(GVC → TBU)    NS Trip Planner v3 (slurp + Filter)
  ├─ fetchTrips(GV  → TBU)    NS Trip Planner v3
  ├─ pickDepartures(ctr,hs)   per-slot picker → 3 Departure[]
  ├─ updateDisplay()          render all sections → display.display()
  └─ goToSleep(900)           deep sleep 15 min; wakeup restarts setup()
```

`loop()` is intentionally empty — deep sleep re-enters `setup()` on each cycle.

---

## Configuration

All constants live in **`config.h`** (gitignored — see `config.h.example` for the template). Secrets (WiFi password, NS API key) live in **`secrets.h`** (also gitignored and read-deny-listed for Claude).

Key settings:
| Constant | Purpose |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | Network credentials (in `secrets.h`) |
| `NS_API_KEY` | NS Dutch Railways API key (in `secrets.h`) |
| `LATITUDE` / `LONGITUDE` | Location for Open-Meteo |
| `TIMEZONE` | POSIX TZ string (NOT IANA — see TZ note below) |
| `SLEEP_DURATION` | Seconds between updates (default 900) |
| `NIGHT_START` / `NIGHT_END` | Hours for night mode (default 23–7) |
| `STATION_CODE_CENTRAL` / `STATION_CODE_HS` / `STATION_CODE_DESTINATION` | NS station codes |
| `DEBUG_LOG` | Optional `#define DEBUG_LOG 1` — enables Serial output at 115200 baud |
| `WIFI_STATIC_IP` / `WIFI_GATEWAY` / `WIFI_SUBNET` / `WIFI_DNS` | Optional static IP (saves ~1–2 s DHCP per wake) |
| `FULL_REFRESH_EVERY` | Wakes between full e-ink refreshes (default 4) |

---

## APIs

| API | Auth | URL |
|---|---|---|
| Open-Meteo (hourly 24h + daily 7d forecast) | None (free) | `api.open-meteo.com/v1/forecast` |
| Buienradar feed (live KNMI station observations) | None (free, attribution required for commercial use) | `data.buienradar.nl/2.0/feed/json` |
| Buienradar raintext (2h precipitation nowcast) | None (free) | `gpsgadget.buienradar.nl/data/raintext?lat=…&lon=…` |
| NS Trip Planner v3 (GVC→TBU and GV→TBU) | `Ocp-Apim-Subscription-Key` header | `gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips` |

All HTTP calls use `WiFiClientSecure` with `client.setInsecure()` (no certificate validation — known limitation), an 8 s timeout, and up to 3 retries via `httpGetWithRetry()`.

**Parse pattern: slurp before parse.** Every HTTPS JSON call (Open-Meteo, Buienradar, Trip Planner) reads the full body into a `String` first and then `deserializeJson(...)`. Streaming straight from `getStream()` over `WiFiClientSecure` intermittently returns `IncompleteInput` when the TLS buffer drains mid-parse — proven on every endpoint that's been tried. The Trip Planner call keeps memory bounded by pairing slurp with `DeserializationOption::Filter` so only the fields the picker actually reads land in the JsonDoc.

**Buienradar mapping & fallback:** the alphabetic iconcode (e.g. `a` = sunny, `j` = clear with high cirrus, `p` = overcast) is translated directly to the dashboard's `WeatherCategory` enum in `categorizeBuienradarIcon()` and flows through the rest of the pipeline as that enum — no synthetic-WMO round-trip (that intermediate hop was removed once Open-Meteo stopped being a current-conditions source). On any Buienradar fetch failure, the dashboard restores the last successful values from `RTC_DATA_ATTR brCache` (separate `nowValid` / `rainValid` flags) rather than falling back to Open-Meteo. Cold boot with a failed first fetch shows the existing "Weather data unavailable" branch and self-heals next wake.

**Buienradar consensus picker (TO REVISIT):** `fetchBuienradarNow` does **not** just take the single nearest station — that strategy was brittle to single-sensor outliers (observed 2026-05-25: Voorschoten at 9 km reported OVERCAST while Rotterdam, Hoek van Holland, Schiphol and Lopik all within 50 km reported CLEAR, and a blue-sky day rendered with a cloud icon). The current picker collects the `BUIENRADAR_MAX_CANDIDATES` nearest valid stations, votes the mode of `WeatherCategory` across those within `BUIENRADAR_CONSENSUS_KM` (default 30 km, ties broken by proximity), and then sources temp/wind/icon from the closest station that voted for the winning category. Known caveats: (1) AWS-class stations like Voorschoten and Rotterdam Geulhaven use cheaper optical sensors than the KNMI synoptic stations and probably deserve less weight, not equal weight — we currently treat them as equals; (2) mode-blending lags real frontal passages by one wake; (3) categorical ties on transitional days can flicker. Worth revisiting once a week or two of multi-station log data exists — at that point a "trust the synoptic stations, ignore AWS-only" rule may be simpler and more accurate than voting. Constants live in `config.h`; debug logs every candidate when `DEBUG_LOG` is on.

---

## Display Layout (800×600px, 1-bit)

```
y=0    ┌─ MASTHEAD: greeting + date + sun/moon arc with current dot ───┐
y=92   ├─ thick rule (2 px) ──────────────────────────────────────────┤
y=112  │ WEATHER  |   RAIN COMING / NEXT HOURS DRY                   │
y=125  │ 128px icon  │  axes + 12-pt rain chart (Bayer fill) OR      │
       │ big temp °  │  24h temp curve with sunrise/sunset guides    │
y=232  │ wind arrow + "X km/h NE"                                    │
y=305  ├─ dotted divider ─────────────────────────────────────────────┤
y=324  │ WEEK — 7 cells × 102 px, day name + 48 icon + range bar     │
y=455  ├─ dotted divider ─────────────────────────────────────────────┤
y=474  │ DEPARTURES · TO BREDA — 3 cards × 220 px (CTR or HS pill)   │
y=590  └─ FOOTER: updated HH:MM + battery icon ──────────────────────┘
```

All Y coordinates are absolute; section comments in `C_Display.ino` annotate each band.

---

## Fonts

Inter (OFL) converted from TTF via [rop.nl/truetype2gfx](https://rop.nl/truetype2gfx/). Headers in `Fonts/`:

| File | Used for |
|---|---|
| `Inter_Bold48pt7b.h` | Hero temperature |
| `Inter_Bold18pt7b.h` | Greeting |
| `Inter_Bold12pt7b.h` | Train time |
| `Inter_Bold9pt7b.h` | Week day names, max-temp labels, transfer warnings, train track |
| `Inter_Regular18pt7b.h` | (kept for fallback / future) |
| `Inter_Regular12pt7b.h` | Failure-state messages |
| `Inter_Regular9pt7b.h` | All body text, small caps, chart labels, date |

**Sizing**: Adafruit GFX point sizes don't map 1:1 to CSS pixels — `9pt7b` cap-height ≈ 14 px, `12pt7b` ≈ 17, `18pt7b` ≈ 26, `48pt7b` ≈ 64. Pick whichever lands closest to the mockup's `font-size`.

**Special glyphs** not in the ASCII-only headers are drawn as primitives:
- `°` → `drawDegreeRing(cx, cy, outerR, innerR)` — filled black ring + white inner
- `·` (middot) → small `fillCircle`
- `→` (right arrow) → `drawRightArrow` (7×3 px)

---

## Icons

All bitmaps are in `icons.h` stored in PROGMEM:
- **128×128**: current-weather variants (sun, cloud_sun, cloud_moon, etc.)
- **64×64**: source bitmaps that the 48×48 set is derived from. No live consumer in the firmware after the redesign — kept so the downscale script can regenerate the 48 set without re-tracing originals.
- **48×48**: week-strip forecast variants (auto-generated by `design/downscale_icons.py`)

When accessing bitmap data directly, always use `pgm_read_byte()`.

To regenerate the 48×48 set after editing the 64×64 sources:
```sh
python design/downscale_icons.py
# then append design/icons_48.generated.h to icons.h (or manually replace)
```

---

## Build & Flash

**IDE:** Arduino IDE 2.x
**Board:** Inkplate6 (via e-Radionica board manager URL)
**Required libraries:**
- `Inkplate` (e-Radionica)
- `ArduinoJson` v7 (NOT v6 — API differs: use `JsonDocument`, not `DynamicJsonDocument`)
- `WiFi`, `WiFiClientSecure`, `HTTPClient` (built into ESP32 core)

**Flash steps:**
1. Copy `config.h.example` → `config.h` and fill in credentials
2. Create `secrets.h` with `#define WIFI_SSID "..."`, `#define WIFI_PASSWORD "..."`, `#define NS_API_KEY "..."`
3. Open `Dashboard.ino` in Arduino IDE (all `.ino` files are included automatically)
4. Select board: Inkplate6, correct COM port
5. Upload
6. (Optional) Set `#define DEBUG_LOG 1` in `config.h` to see serial logs at 115200 baud

---

## Known Issues & Limitations

### Security
- `config.h` and `secrets.h` must NOT be committed.
- No SSL certificate validation (`setInsecure()`) on any HTTPS call.

---

## Working with Claude Code

- **ArduinoJson v7**: Use `JsonDocument doc;` (no size arg). Fields: `doc["key"] | default`. Arrays: `doc["arr"].as<JsonArray>()`.
- **HTTPS JSON parsing — slurp then parse, always.** Streaming straight from `http.getStream()` over `WiFiClientSecure` returned `IncompleteInput` intermittently on every endpoint tried (Open-Meteo, Buienradar, Trip Planner), because the TLS buffer drains mid-parse. The fix is to read the body into a `String` first, then `deserializeJson(doc, response)`. For large payloads where the parsed tree would push heap pressure (Trip Planner's ~90 KB body), keep the *parse tree* small by pairing slurp with `DeserializationOption::Filter` — only the fields the picker actually reads land in the JsonDoc. Measured Trip Planner heap delta: 153 KB free → 101 KB during fetch → 153 KB free after. Plenty of headroom.
- **PROGMEM**: All icon arrays use `PROGMEM`. If adding new bitmaps, declare with `const uint8_t PROGMEM name[] = {...};` and access with `pgm_read_byte()`.
- **Deep sleep — RTC RAM persists**: Every wake is a fresh `setup()` call, so heap state is lost. **But** `RTC_DATA_ATTR` variables survive deep sleep — used today for `wakeCounter`, and a good fit for any cache that should outlive the cycle (last-known-good departures, week forecast). Plain globals do NOT persist.
- **String vs char[]**: Prefer `char buf[N]` + `sprintf` over `String` objects on the heap; the ESP32 has limited RAM and heap fragmentation is a real risk on long-running embedded systems.
- **1-bit display**: All drawing is BLACK or WHITE only. `display.display()` causes a full e-ink refresh (~1–2 s, some flicker). Partial refresh runs in between (`FULL_REFRESH_EVERY` controls cadence).
- **Timezone**: System time uses `configTzTime(TIMEZONE, ...)`, so `TIMEZONE` must be a **POSIX TZ string** (e.g. `"CET-1CEST,M3.5.0,M10.5.0/3"`) — ESP32 has no IANA tzdb, so `"Europe/Amsterdam"` silently falls back to UTC. Open-Meteo URLs use `timezone=auto` (derived from lat/lon) so they don't depend on `TIMEZONE`. Trip Planner ISO timestamps are parsed by `parseISOToLocal()` (treats fields as local since both device and API agree on Amsterdam TZ).
- **Pixel-accurate layout**: The display sections use absolute Y coordinates with section-band comments in `C_Display.ino`. Treat those comments as the layout contract — adjusting one band without updating its neighbours will silently overlap content.
- **OTA safety — boot-path discipline (when OTA is live):** the device is mounted behind glass on the wall. Once OTA is enabled, any change tagged for release that prevents `setup()` from reaching the end of `updateDisplay()` will trigger app-level rollback at best, or require physical reflash at worst. Treat boot-path code (`initHardware`, `handleNightMode`, WiFi/NTP, anything before `updateDisplay()` returns) as load-bearing. Layout/editorial changes inside `C_Display.ino` are low-risk; changes to network code, hardware init, or `setup()` flow need a local USB test before tagging. If a change adds a new required `config.h` field, update the CI-injected `config.h` in the same commit — CI must remain the source of truth for the build. Never reinterpret the semantics of an existing config field without renaming it (silent semantic drift is the one failure mode that bypasses rollback because nothing crashes). See [docs/ota-updates-plan.md](docs/ota-updates-plan.md).

---

## Non-obvious gotchas (spike findings worth preserving)

These were discovered during integration spikes and are not derivable from the code alone. Keeping them so a future change doesn't re-discover them the hard way.

**NS Trip Planner v3:**
- The existing Reisinformatie v2 API key works for v3 — no separate portal subscription needed.
- Den Haag HS is `GV`, not `GVH`. `GVH` returns HTTP 400.
- Every DH → TBU trip has **exactly 2 legs** (IC to Breda, SPR Breda → TBU). The single-leg branch in `fetchTrips` is defensive only.
- Payloads are ~86 KB (GVC→TBU) and ~94 KB (GV→TBU) — slurp+Filter is needed; raw slurp into a `JsonDocument` blows heap.
- NS returns multiple "routing options" for the same physical train (different via-station hashes → identical `plannedDepartureISO`). The picker in `A_Calculations.ino` dedups by ISO before assigning slots.
- ISO timestamps come with offsets *without* a colon (`+0200`, not `+02:00`). Both device and API use Amsterdam local time, so `parseISOToLocal` treats the wall-clock fields as local and ignores the offset.

**Buienradar feed schema:**
- The weather icon is exposed as an *image URL* (`.../weather/30x30/aa.png`) — there is no `iconcode` field. Filename stem = code.
- Icon codes are doubled letters for the day variant (`aa`, `bb`, `cc`) and single for the night variant (`a`, `b`). `extractBuienradarIconCode` collapses doubled letters to single before lookup. `cc` is the only multi-char code that stays distinct.
- `winddirectiondegrees` is exposed directly as an integer (0–360). `winddirection` (Dutch cardinal string) is only used as fallback.
- `feeltemperature` is lowercase (not `feelTemperature`). Not used by the dashboard.

**CI / arduino-cli build gotchas:**
- **FQBN for Inkplate6 is `Inkplate6V2`, NOT `Inkplate6`.** The Soldered "Inkplate_Boards:esp32" package contains both. The legacy `Inkplate6` entry has `build.board=ESP32_DEV` which fails to define the `ARDUINO_INKPLATE6` macro that the v11+ Inkplate library's `driverSelect.h` requires — compile dies with `#error "Board not selected!"`. The IDE's "Soldered Inkplate6" picker silently selects `Inkplate6V2` (which has `build.board=INKPLATE6V2` → defines `ARDUINO_INKPLATE6V2`). Lost ~1 hour to this in Phase 1.
- **arduino-cli requires the sketch folder name to match the main `.ino` filename.** Repo checks out as `inkplate-dashboard/` but the main file is `Dashboard.ino`. CI workaround: copy sketch files into `sketch/Dashboard/` before invoking `arduino-cli compile`.
- **`config.h` and `secrets.h` are gitignored, but CI must own them as the source of truth.** Both are stored as whole-file Actions secrets (`CONFIG_H`, `SECRETS_H`) and written to disk in a workflow step before compile. Never reinterpret semantics of an existing `config.h` field without renaming — see "boot-path discipline" above for why.
- **`FIRMWARE_VERSION` is injected via a CI-generated `firmware_version.h`** (also gitignored). Local builds use the `"dev"` fallback in `Dashboard.ino`. Don't reference `FIRMWARE_VERSION` from code paths that must run on first-ever boot before the OTA framework lands — guard with `#ifdef` if in doubt.

**Battery optimizations consciously skipped:**
- TLS cert pinning — small power win, big code/maintenance cost. Defer until `setInsecure()` stops working (which it isn't).
- CPU clock below 80 MHz — causes WiFi instability.
- Region-targeted partial refresh — ghosting risk with Bayer-dithered fills is too high.
- Sleep current optimization — ~30–40 µA is already near the floor for Inkplate 6's onboard regulators.
