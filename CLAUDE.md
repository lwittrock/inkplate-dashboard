# Inkplate 6 Dashboard — CLAUDE.md

## Project Overview

E-paper weather + train dashboard running on an **Inkplate 6** (ESP32-based, 800×600px 1-bit e-ink display). The device wakes every 15 minutes, fetches live data, renders a full-screen editorial layout, then deep-sleeps until the next cycle. Night mode (23:00–07:00) skips data fetching and sleeps through the night.

**Location:** Delft, Netherlands
**Origin stations:** Den Haag Centraal (GVC) and Den Haag HS (GV)
**Destination:** Tilburg Universiteit (TBU) — both origins queried via NS Trip Planner v3, a per-slot picker substitutes HS trips when a Centraal slot is disrupted.

**Design source of truth:** [design/mockup.html](design/mockup.html) — open in a browser at native 800×600 to see the editorial layout. The redesign rationale and per-section spec live in [docs/redesign-plan.md](docs/redesign-plan.md).

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
  ├─ initHardware()         WiFi connect + NTP sync (POSIX TZ)
  ├─ handleNightMode()      deep sleep if 23:00–07:00
  ├─ fetchOpenMeteo()       current + 24h hourly + 7-day daily + 15-min rain
  ├─ fetchTrips(GVC → TBU)  NS Trip Planner v3 (streaming + Filter)
  ├─ fetchTrips(GV  → TBU)  NS Trip Planner v3
  ├─ pickDepartures(ctr,hs) per-slot picker → 3 Departure[]
  ├─ updateDisplay()        render all sections → display.display()
  └─ goToSleep(900)         deep sleep 15 min; wakeup restarts setup()
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
| Open-Meteo (current + hourly 24h + daily 7d + 15-min rain) | None (free) | `api.open-meteo.com/v1/forecast` |
| NS Trip Planner v3 (GVC→TBU and GV→TBU) | `Ocp-Apim-Subscription-Key` header | `gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips` |

All HTTP calls use `WiFiClientSecure` with `client.setInsecure()` (no certificate validation — known limitation), an 8 s timeout, and up to 3 retries via `httpGetWithRetry()`.

**Two parse patterns coexist** — see "Working with Claude Code" below for when to use which:
- Open-Meteo (~10–20 KB payload): slurp into `String`, then parse.
- Trip Planner (~90 KB payload): stream directly with `DeserializationOption::Filter` to keep heap under control.

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

All Y coordinates are absolute (pixel-accurate to `design/mockup.html`). See [docs/redesign-plan.md](docs/redesign-plan.md) for the per-section spec.

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
- **64×64**: forecast variants (kept for fallback)
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
- **HTTPS JSON parsing — two patterns**:
  - **Small payload (slurp)**: `String response = http.getString(); deserializeJson(doc, response);` — what Open-Meteo uses. Easy and reliable for ≤ ~20 KB responses.
  - **Large payload (stream + Filter)**: `deserializeJson(doc, http.getStream(), DeserializationOption::Filter(filter));` — what Trip Planner uses. The ~90 KB NS payload would push heap pressure if slurped, so we build a filter that keeps only the fields we need (`trips[].legs[].{cancelled, origin.plannedDateTime, …}`) and stream-parse. Verified working on Inkplate WiFiClientSecure with ~50 KB transient heap delta. If streaming fails on a new endpoint with `IncompleteInput`, slurp+filter is a safe fallback.
- **PROGMEM**: All icon arrays use `PROGMEM`. If adding new bitmaps, declare with `const uint8_t PROGMEM name[] = {...};` and access with `pgm_read_byte()`.
- **Deep sleep — RTC RAM persists**: Every wake is a fresh `setup()` call, so heap state is lost. **But** `RTC_DATA_ATTR` variables survive deep sleep — used today for `wakeCounter`, and a good fit for any cache that should outlive the cycle (last-known-good departures, week forecast). Plain globals do NOT persist.
- **String vs char[]**: Prefer `char buf[N]` + `sprintf` over `String` objects on the heap; the ESP32 has limited RAM and heap fragmentation is a real risk on long-running embedded systems.
- **1-bit display**: All drawing is BLACK or WHITE only. `display.display()` causes a full e-ink refresh (~1–2 s, some flicker). Partial refresh runs in between (`FULL_REFRESH_EVERY` controls cadence).
- **Timezone**: System time uses `configTzTime(TIMEZONE, ...)`, so `TIMEZONE` must be a **POSIX TZ string** (e.g. `"CET-1CEST,M3.5.0,M10.5.0/3"`) — ESP32 has no IANA tzdb, so `"Europe/Amsterdam"` silently falls back to UTC. Open-Meteo URLs use `timezone=auto` (derived from lat/lon) so they don't depend on `TIMEZONE`. Trip Planner ISO timestamps are parsed by `parseISOToLocal()` (treats fields as local since both device and API agree on Amsterdam TZ).
- **Pixel-accurate layout**: The display sections use absolute Y coordinates copied from `design/mockup.html`. Cross-check the mockup before moving anything.
