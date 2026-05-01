# Inkplate 6 Dashboard — CLAUDE.md

## Project Overview

E-paper weather + train dashboard running on an **Inkplate 6** (ESP32-based, 800×600px 1-bit e-ink display). The device wakes every 15 minutes, fetches live data from three APIs, renders a full-screen layout, then deep-sleeps until the next cycle. Night mode (23:00–07:00) skips data fetching and sleeps through the night.

**Location:** Delft, Netherlands  
**Destination trains:** Den Haag Centraal (GVC) and Den Haag HS (GV) → Breda

---

## Architecture

The sketch is split into four `.ino` files (all compiled together by Arduino IDE):

```
Dashboard.ino        — Entry point: data structures, setup(), loop()
A_Calculations.ino   — Weather categorisation (calculateDailyWeather), delay calc
B_Network.ino        — WiFi, NTP, three API calls
C_Display.ino        — All rendering: fonts, icons, layout sections
```

**Data flow:**
```
setup()
  ├─ initHardware()         WiFi connect + NTP sync
  ├─ handleNightMode()      deep sleep if 23:00–07:00
  ├─ fetchOpenMeteo()        single Open-Meteo call: current + 7-day forecast
  │                          + 15-min precipitation (12 intervals = 3h)
  ├─ getTrains(GVC)          NS API: departures from Den Haag Centraal
  ├─ getTrains(GV)           NS API: departures from HS (only if Centraal empty/all cancelled)
  ├─ updateDisplay()         render all sections → display.display()
  └─ goToSleep(900)          deep sleep 15 min; wakeup restarts setup()
```

`loop()` is intentionally empty — deep sleep re-enters `setup()` on each cycle.

---

## Configuration

All constants live in **`config.h`** (gitignored — see `config.h.example` for the template).

Key settings:
| Constant | Purpose |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | Network credentials |
| `NS_API_KEY` | NS Dutch Railways API key |
| `LATITUDE` / `LONGITUDE` | Location for Open-Meteo |
| `TIMEZONE` | IANA timezone string (e.g. `Europe/Amsterdam`) |
| `SLEEP_DURATION` | Seconds between updates (default 900) |
| `NIGHT_START` / `NIGHT_END` | Hours for night mode (default 23–7) |
| `STATION_CODE_CENTRAL` / `STATION_CODE_HS` | NS station codes |
| `TRAIN_DESTINATION` | Lowercase destination filter string (e.g. `"breda"`) |

> Note: `BATTERY_GOOD/MEDIUM/LOW` still exist in `config.h.example` but are unused — `drawFooter()` now renders a percentage-based icon mapped from raw voltage (3.5 V = 0 %, 4.2 V = 100 %).

---

## APIs

| API | Auth | URL |
|---|---|---|
| Open-Meteo (weather + forecast + 15-min rain, single combined request) | None (free) | `api.open-meteo.com/v1/forecast` |
| NS Reisinformatie v2 | `Ocp-Apim-Subscription-Key` header | `gateway.apiportal.ns.nl/reisinformatie-api/api/v2/departures` |

All HTTP calls use `WiFiClientSecure` with `client.setInsecure()` (no certificate validation — known limitation), an 8 s timeout, and up to 3 retries via `httpGetWithRetry()`. Responses are streamed straight from `http.getStream()` into ArduinoJson — no intermediate `String` buffer.

---

## Display Layout (800×600px, 1-bit)

```
y=40   ┌─ HEADER (date, time, update time) ───────────────────────────┐
y=90   ├───────────────────────────────────────────────────────────────┤
y=105  │ WEATHER: 128×128 icon | temp (48pt bold) | wind description   │
       │          Rain chart (330×120px, 3-hour 15-min intervals)      │
y=275  ├───────────────────────────────────────────────────────────────┤
y=285  │ FORECAST: 7 days × 105px spacing (64×64 icon, max/min temps)  │
y=455  ├───────────────────────────────────────────────────────────────┤
y=465  │ TRAINS: up to 3 departures, time + track + delay              │
y=590  └─ FOOTER: battery voltage & status ────────────────────────────┘
```

Layout constants are defined at the top of `C_Display.ino` and auto-calculate Y positions from section heights and gaps.

---

## Fonts

Fonts are in `Fonts/` (Adafruit GFX format):

| File | Used for |
|---|---|
| `FreeSansBold48pt7b.h` | Current temperature |
| `FreeSans18pt7b.h` | Header date/time, train times |
| `FreeSans12pt7b.h` | Labels, forecast text, wind |
| `FreeSans9pt7b.h` | Small text, rain chart labels, train details |

---

## Icons

All bitmaps are in `icons.h` stored in PROGMEM. Sizes: 128×128 (current weather), 64×64 (forecast), 32×32 (train), 16×16 (raindrop).

When accessing bitmap data directly, always use `pgm_read_byte()`.

`icon_wind_128` and `icon_wind_64` are defined but currently unused.

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
2. Open `Dashboard.ino` in Arduino IDE (all `.ino` files are included automatically)
3. Select board: Inkplate6, correct COM port
4. Upload
5. Open Serial Monitor at 115200 baud to see boot logs

---

## Known Issues & Limitations

### Security
- `config.h` must NOT be committed — it contains WiFi password and NS API key.
- No SSL certificate validation (`setInsecure()`) on any HTTPS call.

### Dead code
- `icon_wind_128` / `icon_wind_64` defined in `icons.h` but never drawn.

---

## Working with Claude Code

- **ArduinoJson v7**: Use `JsonDocument doc;` (no size arg). Fields: `doc["key"] | default`. Arrays: `doc["arr"].as<JsonArray>()`.
- **HTTPS JSON: slurp, don't stream.** Always parse via `String response = http.getString(); deserializeJson(doc, response);`. Do NOT use `deserializeJson(doc, http.getStream())` — on `WiFiClientSecure` the TLS layer drains the TCP buffer faster than the next chunk arrives and ArduinoJson sees a premature EOF (`IncompleteInput`). Tried on the merged Open-Meteo call and it failed every cycle ("Weather Sync Error"). The heap cost of the intermediate `String` is acceptable; if memory ever gets tight, prefer `DeserializationOption::Filter` over streaming.
- **PROGMEM**: All icon arrays use `PROGMEM`. If adding new bitmaps, declare with `const uint8_t PROGMEM name[] = {...};` and access with `pgm_read_byte()`.
- **Deep sleep / no persistent RAM**: Every wake is a fresh `setup()` call. There is no state between cycles — don't rely on global variables surviving sleep.
- **String vs char[]**: Prefer `char buf[N]` + `sprintf` over `String` objects on the heap; the ESP32 has limited RAM and heap fragmentation is a real risk on long-running embedded systems.
- **1-bit display**: All drawing is BLACK or WHITE only. `display.display()` causes a full e-ink refresh (slow ~1-2 sec, some flicker). Partial refresh is not used.
- **Timezone**: System time uses `setenv("TZ", TIMEZONE, 1) + tzset() + configTime(0, 0, "pool.ntp.org")`, so `TIMEZONE` must be a **POSIX TZ string** (e.g. `"CET-1CEST,M3.5.0,M10.5.0/3"`) — ESP32 has no IANA tzdb, so `"Europe/Amsterdam"` silently falls back to UTC. Open-Meteo URLs use `timezone=auto` (derived from lat/lon) so they don't depend on `TIMEZONE`.
