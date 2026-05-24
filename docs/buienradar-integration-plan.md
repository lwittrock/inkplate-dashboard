# Buienradar Integration Plan

## Why this change

Open-Meteo's `current` block is model-derived (interpolated from the latest run of a global numerical weather model). For the Netherlands this regularly produces "phantom clouds" — the dashboard reports `cloudy` while the sky is perfectly clear, because a coarse global model misjudged the timing of a front or counted high-altitude cirrus as full cloud cover. Buienradar exposes live 10-minute KNMI ground-station telemetry plus a hyper-local 2-hour rain nowcast derived from KNMI's dual precipitation radars. For a dashboard that lives next to a window, observation-based data is the right primitive.

This change replaces both Open-Meteo blocks in one branch (no staged rollout):
- the `current` block (temperature, wind speed, wind direction, weather code) → Buienradar `data.buienradar.nl/2.0/feed/json`
- the `minutely_15` precipitation block → Buienradar `gpsgadget.buienradar.nl/data/raintext`

Open-Meteo continues to provide `hourly` (24h temperature curve) and `daily` (7-day forecast). Its now-unused `current` and `minutely_15` URL parameters are stripped to shrink the response.

---

## Locked-in decisions (from the questions you answered)

| Decision | Choice |
|---|---|
| Shipping cadence | Stage 1 + Stage 2 in one branch |
| Attribution | None (personal project) |
| Source-tag in footer | None (no `· BR` / `· OM`) |
| Open-Meteo fallback on Buienradar failure | None — use RTC RAM cache of last known good |
| Cache granularity | Current weather and rain cached separately |
| Heap strategy | Slurp then parse (matches OM pattern) |
| Rain chart density | 24 bars at 5-min spacing |
| Open-Meteo URL | Strip `current=…` and `minutely_15=…` |
| Cold boot + first fetch fail | Existing `"Weather data unavailable"` branch |
| Dry-state outlook (no rain in 2h) | Keep — switch chart to 24h temp curve |

---

## Architecture

```
                          ┌──────────────────────────────┐
                          │  setup() per wake            │
                          └──────────────────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
   ┌────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
   │ fetchOpenMeteo()   │  │ fetchBuienradarNow() │  │ fetchBuienradarRain()│
   │ hourly + daily     │  │ JSON 40–60 KB        │  │ text ~250 B          │
   │ (no current,       │  │ → nearest station    │  │ → 24 × mm/h, HH:MM   │
   │  no minutely_15)   │  │ → temp, wind, code   │  │                      │
   └────────────────────┘  └──────────────────────┘  └──────────────────────┘
              │                        │                        │
              │            success     │     success            │     success
              │            ──────►     │     ──────►            │     ──────►
              │                        │                        │
              │            fail        │     fail               │     fail
              │            keep        │     read from          │     read from
              │            (forecast   │     RTC cache.now      │     RTC cache.rain
              │             section)   │                        │
              │                        ▼                        ▼
              │              ┌────────────────────────────────────────┐
              │              │ RTC_DATA_ATTR BuienradarCache cache    │
              │              │   .nowValid, .now (5 fields)           │
              │              │   .rainValid, .rain (24 mm/h + labels) │
              │              │   survives deep sleep, ~700 B          │
              │              └────────────────────────────────────────┘
              │
              ▼
        updateDisplay(...)
```

Two independent Buienradar fetches, two cache regions. A 250-byte text fetch failing does not force the 5 current-weather fields to render from cache, and vice versa.

---

## B1 — Endpoints

| Purpose | URL | Auth | Format | Size |
|---|---|---|---|---|
| Current conditions | `https://data.buienradar.nl/2.0/feed/json` | none | JSON | ~40–60 KB |
| 2h rain nowcast | `https://gpsgadget.buienradar.nl/data/raintext?lat={LAT}&lon={LON}` | none | text | ~250 B |

Both HTTPS. Existing `WiFiClientSecure` + `setInsecure()` pattern applies.

Add to [config.h.example](../config.h.example):
```cpp
#define BUIENRADAR_FEED_URL "https://data.buienradar.nl/2.0/feed/json"
#define BUIENRADAR_RAIN_URL "https://gpsgadget.buienradar.nl/data/raintext?lat=%.2f&lon=%.2f"
#define BUIENRADAR_STALE_MIN 60   // skip stations whose timestamp is older than this
```

---

## Schema notes (verified against live `data.buienradar.nl/2.0/feed/json`)

Several fields the Gemini transcript described do not exist in the actual feed. Verified by curling the live endpoint:

- **No `iconcode` field.** The icon is exposed only as `iconurl` (e.g. `https://cdn.buienradar.nl/resources/images/icons/weather/30x30/aa.png`). The code is the filename stem.
- **Icon codes are doubled letters for the day variant** (`aa`, `bb`, `cc`) and single for the night variant (`a`, `b`). Both forms map to the same `WeatherCategory`. `cc` is the only legitimate two-letter code that stays distinct in the mapping (heavy-overcast); all other doubled letters collapse to their single-letter equivalent before lookup.
- **`winddirectiondegrees` is exposed directly as an integer** (0–360). The Dutch cardinal string (`winddirection`) is also present but only needed as a fallback.
- **`feeltemperature` is lowercase** (not `feelTemperature` as Gemini claimed). The dashboard doesn't use it.
- **Host is `cdn.buienradar.nl`** for icon URLs (not `www.buienradar.nl`).

Full first-station key list seen in the live feed:
```
$id, stationid, stationname, lat, lon, regio, timestamp,
weatherdescription, iconurl, fullIconUrl, graphUrl,
winddirection, airpressure, temperature, groundtemperature,
feeltemperature, visibility, windgusts, windspeed, windspeedBft,
humidity, precipitation, sunpower, rainFallLast24Hour,
rainFallLastHour, winddirectiondegrees
```

---

## B2 — Buienradar icon code → WeatherCategory mapping

| iconcode | Dutch | → WeatherCategory | useSunnyVariant |
|---|---|---|---|
| `a` | Vrijwel onbewolkt | CLEAR | n/a |
| `b` | Licht bewolkt | PARTLY_CLOUDY | n/a |
| `c` | Zwaar bewolkt | OVERCAST | n/a |
| `cc` | Zwaar bewolkt (dichter) | OVERCAST | n/a |
| `d` | Afwisselend bewolkt met lokale mist | FOG | n/a |
| `f` | Afwisselend bewolkt met lichte regen | DRIZZLE | true |
| `g` | Bewolkt met kans op onweer | THUNDERSTORM | n/a |
| `h` | Wisselend bewolkt met regen | RAIN | true |
| `i` | Zwaar bewolkt met lichte sneeuwval | SNOW | false |
| `j` | Mix opklaringen en hoge bewolking | CLEAR | n/a |
| `k` | Zwaar bewolkt met wat lichte regen | DRIZZLE | false |
| `l` | Opklaringen en kans op onweersbuien | THUNDERSTORM | n/a |
| `m` | Zwaar bewolkt en regen | RAIN_HEAVY | false |
| `n` | Opklaring en lokaal nevel of mist | FOG | n/a |
| `o` | Mix opklaringen en lage bewolking | PARTLY_CLOUDY | n/a |
| `p` | Bewolkt | OVERCAST | n/a |
| `q` | Zwaar bewolkt en regen | RAIN_HEAVY | n/a |
| `r` | Mix opklaringen en lage bewolking | PARTLY_CLOUDY | n/a |
| `s` | Bewolkt met kans op onweer | THUNDERSTORM | n/a |
| `u` | Afwisselend bewolkt met lichte sneeuwval | SNOW | true |
| `v` | Zware sneeuwval | SNOW | false |
| `w` | Zwaar bewolkt met winterse neerslag | SNOW | false |

Unknown codes → OVERCAST + DBG log. `j` → CLEAR is the most direct "phantom cloud" fix.

Implementation: `categorizeBuienradarIcon(const char* code, bool& useSunnyVariant)` → `WeatherCategory`.

Day/night: Buienradar doesn't distinguish. The existing `drawWeatherIcon128(..., isNight)` already picks moon vs sun. No new logic.

---

## B3 — Wiring category into the existing render path

Use **Option α**: at fetch time, map resolved `WeatherCategory` back to an equivalent synthetic WMO code so `categorizeCurrentWeather(int)` round-trips it. Zero signature changes downstream.

| WeatherCategory | Synthetic WMO code |
|---|---|
| CLEAR | 0 |
| PARTLY_CLOUDY | 2 |
| OVERCAST | 3 |
| FOG | 45 |
| DRIZZLE | 51 |
| RAIN | 61 |
| RAIN_HEAVY | 81 |
| SNOW | 71 |
| THUNDERSTORM | 95 |

---

## B4 — Station selection

```
best = null
for s in actual.stationmeasurements:
    code = extractBuienradarIconCode(s.iconurl)
    if code is "" → skip                                  // precip-only stations
    if s.temperature missing → skip
    if s.timestamp older than BUIENRADAR_STALE_MIN → skip
    d = haversine(LATITUDE, LONGITUDE, s.lat, s.lon)
    if best is null or d < best.distance: best = (s, d)
return best
```

For Delft (52.01, 4.36) the typical winners are Voorschoten (~13 km), Rotterdam-Geulhaven (~16 km), Hoek van Holland (~25 km).

Add `haversineKm(lat1, lon1, lat2, lon2)` to [A_Calculations.ino](../A_Calculations.ino). `float` math.

---

## B5 — Wind direction & speed

Buienradar exposes both:
- `windspeed` — m/s (multiply by 3.6 for km/h)
- `winddirectiondegrees` — integer 0–360 (preferred — used directly)
- `winddirection` — Dutch 16-point cardinal (`"NNO"`, `"ZZW"`, …) — kept only as fallback

`bearingFromDutchCardinal` lives in [A_Calculations.ino](../A_Calculations.ino) for the rare case where `winddirectiondegrees` is absent (8-point resolution but correct):

| | | | |
|---|---|---|---|
| N=0 | NNO=23 | NO=45 | ONO=68 |
| O=90 | OZO=113 | ZO=135 | ZZO=158 |
| Z=180 | ZZW=203 | ZW=225 | WZW=248 |
| W=270 | WNW=293 | NW=315 | NNW=338 |

---

## B6 — Heap & parse strategy

Slurp the JSON body into a `String`, then parse — same pattern as `fetchOpenMeteo` and the just-fixed `fetchTrips`. ~50 KB transient + JsonDoc; free heap at fetch time is ~150 KB. Comfortable.

The `raintext` body is ~250 bytes — slurp unconditionally.

---

## B7 — RTC cache

```cpp
struct BuienradarCache {
  // Current weather
  bool  nowValid;
  float temp;
  int   weatherCode;     // synthetic WMO from BR mapping
  float wind;            // km/h
  int   windBearing;     // degrees from N

  // Rain nowcast
  bool  rainValid;
  int   rainCount;
  float rainMmh[24];     // intensity in mm/h
  char  rainLabels[24][6];  // "HH:MM\0"
};
RTC_DATA_ATTR BuienradarCache brCache = {};
```

Size: ~700 bytes. Easily fits in RTC slow RAM (8 KB available).

On every successful fetch, copy fresh values into the cache. On failure, copy cache values back into the working variables (if `*Valid`). If neither fresh nor cached, render the existing `weatherOk=false` / `rainOk=false` branches.

The cache lives across deep sleep (`RTC_DATA_ATTR`), but is zero-initialized on cold boot (battery removal / first flash). The very first wake after a cold boot relies on a successful fetch; if that fails, the dashboard renders the existing "Weather data unavailable" message and self-heals on the next wake.

---

## B8 — Rain chart adaptation

Current: `rainData[12]` mm-per-15-min, `timeLabels[12][20]` ISO strings, 30-px bar spacing.

New: `rainData[24]` mm/h, `timeLabels[24][6]` `"HH:MM"`, ~14-px bar spacing.

Threshold rebase (`mm/h ≈ mm-per-15min × 4`):

| | Old (mm-per-15min) | New (mm/h) |
|---|---|---|
| Light | 0.1 | **0.4** |
| Medium | 1.0 | **4.0** |
| Heavy | 2.5 | **10.0** |
| `maxScale` default | 3.0 | **12.0** |

Dry-state trigger: change `rainData[i] >= 0.1f` (in mm-per-15-min) → `rainData[i] >= 0.4f` (in mm/h). Same semantic threshold.

Hour-tick logic: currently parses minute from ISO offset 14. New format is `"HH:MM"`, minute at offset 3. Bar `i` is at minute `5*i` past the first label's time, so detect minute==0 from the label string directly.

---

## B9 — Open-Meteo URL trimming

Remove from URL in [B_Network.ino:118-127](../B_Network.ino):
- `&current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m`
- `&minutely_15=precipitation&forecast_minutely_15=12`

Also remove from `fetchOpenMeteo` body:
- The current-weather parse (lines ~152-156)
- The 15-min rain parse (lines ~228-240)
- The `temp`, `wind`, `currentWeatherCode`, `rainData`, `timeLabels`, `rainCount`, `rainOk` output params

New signature:
```cpp
bool fetchOpenMeteo(WeatherExtras &extras, DayForecast forecast[], int &forecastCount);
```

Saves ~5–8 KB on the OM response.

---

## B10 — Failure modes

| Failure | Behavior |
|---|---|
| `data.buienradar.nl` non-200 or malformed JSON | DBG log, restore `now` from cache (if valid) |
| All stations stale (>60 min) | DBG warning, restore `now` from cache (if valid) |
| Nearest station missing `temperature` | Skip station, try next |
| Unknown `iconcode` | Falls to OVERCAST + DBG log of the letter |
| Wind direction string unrecognized | bearing = -1 → skip station |
| `raintext` non-200 or 0 bytes | DBG log, restore `rain` from cache (if valid) |
| `raintext` malformed lines | Use valid lines only; if 0 valid → cache fallback |
| Cold boot + BR fail + no cache | Existing `"Weather data unavailable"` / dry-outlook branch renders |

---

## Files modified

| File | Change |
|---|---|
| [config.h.example](../config.h.example) | + 3 Buienradar constants |
| [A_Calculations.ino](../A_Calculations.ino) | + `categorizeBuienradarIcon`, `bearingFromDutchCardinal`, `haversineKm`, `categoryToSyntheticWmo` |
| [B_Network.ino](../B_Network.ino) | + `fetchBuienradarNow`, `fetchBuienradarRain`; trim OM URL + parse + signature |
| [Dashboard.ino](../Dashboard.ino) | + `BuienradarCache` RTC struct + RTC instance; rewire `setup()`; bump `rainData[12]→[24]`, `timeLabels[12][20]→[24][6]`; update `fetchOpenMeteo` call site |
| [C_Display.ino](../C_Display.ino) | `timeLabels[][20]→[][6]` in `drawRainChart`, `updateDisplay`; bar spacing 30→14 px; mm/h thresholds; new hour-tick parser; dry-state threshold 0.1→0.4 |

No icons.h changes. No new fonts. No struct refactors of WeatherExtras / DayForecast.

---

## Verification

- Watch serial log over 4–8 wakes: confirm nearest station is sane (~Voorschoten / Rotterdam), no fallbacks.
- Visually compare on-screen condition to Buienradar.nl in the browser at the same moment.
- Force failure by editing `BUIENRADAR_FEED_URL` to a bad host; confirm cache values render after the first successful warm-up wake. Restore URL.
- Cold-boot test: flash, watch first wake. If BR succeeds, weather panel renders normally. If BR fails first try, expect "Weather data unavailable", then second wake recovers.
- During rain: compare 5-min nowcast bars to Buienradar.nl's app graph.
- Heap: confirm `DBG("BR now heap:")` post-parse shows >50 KB free.
