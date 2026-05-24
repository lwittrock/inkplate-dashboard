# Buienradar Integration Plan

## Why this change

Open-Meteo's `current` block is model-derived (interpolated from the latest run of a global numerical weather model). For the Netherlands this regularly produces "phantom clouds" — the dashboard reports `cloudy` while the sky is perfectly clear, because a coarse global model misjudged the timing of a front by an hour, or counted high-altitude cirrus as full cloud cover. Buienradar, by contrast, exposes live 10-minute KNMI ground-station telemetry plus a hyper-local 2-hour rain nowcast derived from KNMI's dual precipitation radars — what the physical sensors actually measure right now. For a dashboard that lives next to a window, observation-based data is the right primitive.

This plan replaces:
- the Open-Meteo `current` block (temperature, wind speed, wind direction, weather code), and
- the Open-Meteo `minutely_15` precipitation block

with Buienradar equivalents, while keeping the Open-Meteo `hourly` temperature curve and `daily` 7-day forecast untouched (those parts of Open-Meteo are fine and Buienradar's forecast surface is sparser).

Open-Meteo remains the **fallback** source for the values Buienradar overwrites — if a Buienradar call fails for any reason, the Open-Meteo values stay in place and the dashboard still renders.

---

## Why this is bigger than it looks

Buienradar is not a drop-in replacement for two structural reasons:

1. **Different data model.** Open-Meteo returns WMO numeric codes (`0`, `2`, `61`, …) that go through `categorizeCurrentWeather(int)` in [A_Calculations.ino](../A_Calculations.ino). Buienradar returns single-letter strings (`a`, `b`, `cc`, …) mapped to Dutch condition phrases ("Zonnig", "Licht bewolkt"). Two different categorization paths must coexist.
2. **Different rain units & cadence.** Open-Meteo `minutely_15` gives **mm-per-15-min** at 15-min spacing covering 3 hours (12 samples). Buienradar `raintext` gives an **encoded intensity integer** at 5-min spacing covering 2 hours (24 samples). Both unit conversion and rebinning are required.

Plus four operational concerns: station selection, fallback chain, attribution (legal), and heap pressure.

This is best implemented as **two sub-stages**, each independently verifiable:

- **Stage 1** — Buienradar replaces current weather only. Rain stays on Open-Meteo.
- **Stage 2** — Buienradar `raintext` replaces the `minutely_15` rain block.

Ship Stage 1 and let it run clean for ~a week before adding Stage 2. Don't merge them into one PR.

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

## B2 — Buienradar iconcode → WeatherCategory mapping

Existing enum: `CLEAR, PARTLY_CLOUDY, OVERCAST, FOG, DRIZZLE, RAIN, RAIN_HEAVY, SNOW, THUNDERSTORM` (see [Dashboard.ino:38-48](../Dashboard.ino)).

The Buienradar icon code letters are not formally published as a stable contract, but the mapping is empirically stable and used by Home Assistant (`mjj4791/python-buienradar`) and other long-running integrations. Cross-referenced against Buienradar's public legend page:

| iconcode | Dutch description | → WeatherCategory | useSunnyVariant |
|---|---|---|---|
| `a` | Vrijwel onbewolkt (zonnig/helder) | CLEAR | n/a |
| `b` | Licht bewolkt | PARTLY_CLOUDY | n/a |
| `c` | Zwaar bewolkt | OVERCAST | n/a |
| `cc` | Zwaar bewolkt (dichter) | OVERCAST | n/a |
| `d` | Afwisselend bewolkt met lokale mist | FOG | n/a |
| `f` | Afwisselend bewolkt met lichte regen | DRIZZLE | **true** (sun + rain) |
| `g` | Bewolkt met kans op onweer | THUNDERSTORM | n/a |
| `h` | Wisselend bewolkt met regen | RAIN | **true** |
| `i` | Zwaar bewolkt met lichte sneeuwval | SNOW | false |
| `j` | Mix van opklaringen en hoge bewolking | CLEAR | n/a |
| `k` | Zwaar bewolkt met wat lichte regen | DRIZZLE | false |
| `l` | Opklaringen en kans op onweersbuien | THUNDERSTORM | n/a |
| `m` | Zwaar bewolkt en regen | RAIN_HEAVY | false |
| `n` | Opklaring en lokaal nevel of mist | FOG | n/a |
| `o` | Mix van opklaringen en lage bewolking | PARTLY_CLOUDY | n/a |
| `p` | Bewolkt | OVERCAST | n/a |
| `q` | Zwaar bewolkt en regen | RAIN_HEAVY | n/a |
| `r` | Mix van opklaringen en lage bewolking | PARTLY_CLOUDY | n/a |
| `s` | Bewolkt met kans op onweer | THUNDERSTORM | n/a |
| `u` | Afwisselend bewolkt met lichte sneeuwval | SNOW | **true** |
| `v` | Zware sneeuwval | SNOW | false |
| `w` | Zwaar bewolkt met winterse neerslag | SNOW | false |

Notes:
- `useSunnyVariant` is irrelevant for the 128-px current-weather icon (it uses isNight day/moon variants only), but is preserved so the same mapping can be reused for the week strip if needed.
- Unknown / new codes → fall back to OVERCAST (safe grey default), log via `DBG`.
- `j` (mostly clear with high cirrus) → CLEAR is the most direct fix for the "phantom cloud" complaint.

Implementation: add to [A_Calculations.ino](../A_Calculations.ino):
```cpp
WeatherCategory categorizeBuienradarIcon(const char* code, bool& useSunnyVariant);
```

---

## B3 — Day/night handling for the icon

Buienradar doesn't distinguish day/night in its iconcode. The existing `drawWeatherIcon128(x, y, weatherCode, isNight)` switches CLEAR→moon and PARTLY_CLOUDY→cloud+moon when `isNight=true`. `isNight` is computed at call site from local time — no change needed.

---

## B4 — Wiring Buienradar's category into the existing render path

Current data flow: `weatherCode` (int) is plumbed from `fetchOpenMeteo` → `updateDisplay` → `drawCurrentWeather` → `drawWeatherIcon128` → `categorizeCurrentWeather(int)`.

Two integration options:

**Option α (recommended for Stage 1, smallest diff):** map the resolved `WeatherCategory` back to an equivalent synthetic WMO code at fetch time. The existing `categorizeCurrentWeather()` round-trips it back to the right category.

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

Pros: zero signature changes; fallback-compatible (Open-Meteo's real WMO code is already there as default).
Cons: marginally indirect.

**Option β:** refactor `drawCurrentWeather` and `drawWeatherIcon128` to accept `WeatherCategory` directly. Cleaner but touches more code.

→ Use **α** for Stage 1. Refactor to β later if iconcode logic grows.

---

## B5 — Station selection

The JSON feed contains ~50 station blocks. Do **not** hardcode `stationid 6215`. Algorithm:

```
best = null
for s in actual.stationmeasurements:
    if s.iconcode is null/empty → skip          // some stations are precip-only
    if s.timestamp older than BUIENRADAR_STALE_MIN → skip
    d = haversine(LATITUDE, LONGITUDE, s.lat, s.lon)
    if best is null or d < best.distance: best = (s, d)
return best
```

For Delft (52.01, 4.36) the nearest stations with full data are typically Voorschoten (52.13, 4.43), Rotterdam-Geulhaven (51.89, 4.31), and Hoek van Holland (51.99, 4.10) — all within ~15 km.

If all stations fail the stale/iconcode filter (rare), fall back to Open-Meteo current values already in memory.

Implementation: add `haversineKm(lat1, lon1, lat2, lon2)` to [A_Calculations.ino](../A_Calculations.ino). Use `float` math — accuracy is irrelevant for ranking stations.

---

## B6 — Wind direction & speed conversions

Buienradar returns:
- `windspeed` — number, **m/s**
- `winddirection` — string, Dutch 16-point compass (e.g. `"NNO"`, `"ZZW"`)

Existing display expects: `wind` in km/h, `windBearing` in degrees-from-north.

Add to [A_Calculations.ino](../A_Calculations.ino):

```cpp
// Returns -1 if the string is unrecognized.
int bearingFromDutchCardinal(const char* s);
```

Mapping (Dutch → bearing degrees, rounded to int):

| String | Bearing | String | Bearing |
|---|---|---|---|
| N | 0 | Z | 180 |
| NNO | 23 | ZZW | 203 |
| NO | 45 | ZW | 225 |
| ONO | 68 | WZW | 248 |
| O | 90 | W | 270 |
| OZO | 113 | WNW | 293 |
| ZO | 135 | NW | 315 |
| ZZO | 158 | NNW | 338 |

Speed conversion: `wind_kmh = windspeed_ms * 3.6f`.

Downstream code (`cardinalCompass()` returning English `"NNW"`) is unchanged: 8-bucket resolution is intentional and matches the existing display style.

---

## B7 — Heap budget & parse strategy

The JSON feed is ~40–60 KB.

| Approach | Heap peak | Reliability on WiFiClientSecure | Verdict |
|---|---|---|---|
| Slurp into `String`, then parse (Open-Meteo pattern) | ~60 KB transient + JsonDoc | Known good — Open-Meteo at [B_Network.ino:137-140](../B_Network.ino) explicitly warns against streaming | **Recommended for Stage 1** |
| Stream + Filter (Trip Planner pattern) | ~5 KB filtered JsonDoc | Trip Planner verified on 90 KB; might still IncompleteInput intermittently | Use only if slurp pressures heap |

Free heap after WiFi connect is typically 180–220 KB, so 60 KB slurp is comfortable. Start with slurp; if `DBG("Buienradar heap:")` shows < 30 KB free post-parse, switch to Filter targeting `actual.stationmeasurements[].{stationid, stationname, lat, lon, iconcode, weatherdescription, timestamp, temperature, feelTemperature, humidity, windspeed, winddirection}`.

The `raintext` body is ~250 bytes — slurp unconditionally.

---

## B8 — Fallback chain

Fetch order in `setup()` (in [Dashboard.ino](../Dashboard.ino)):

```
1. fetchOpenMeteo(...)             → populates temp, weatherCode, wind, windDirection, rainData[12]
2. fetchBuienradarCurrent(...)     → overwrites temp, weatherCode, wind, windDirection on success
3. fetchBuienradarRain(...)        → (Stage 2) overwrites rainData + rainCount + timeLabels on success
```

If step 2 or 3 fails, the Open-Meteo values from step 1 stay in place. Failures must be silent (DBG log only) — never `showError()`; the dashboard must always render.

**Visual indicator:** append `" · BR"` or `" · OM"` to the footer "updated HH:MM" line to make silent fallbacks visible at a glance.

---

## B9 — Rain chart adaptation (Stage 2 only)

Current rain chart ([C_Display.ino:635-…](../C_Display.ino)) consumes:
- `rainData[12]` — mm in each 15-min slot
- `timeLabels[12][20]` — ISO timestamps like `"2026-05-24T19:15"`
- `rainCount` — usually 12, covers 3 h forward

Buienradar `raintext` gives:
- 24 lines, format `VVV|HH:MM` (e.g. `035|19:25`)
- Intensity: `mm/h = pow(10, (V - 109) / 32.0)`, clamped to 0 if V==0
- 5-min spacing, covers 2 h forward

**Cadence mismatch.** 24 × 5 min vs 12 × 15 min. Recommended fix:
- (a) Bump array sizes to 24 and rescale the chart's x-axis spacing (24 bars instead of 12, half as wide each). More information density; a 5-min nowcast is exactly what Buienradar is famous for.
- (b) Aggregate 24 samples into 8 × 15-min bins by averaging — chart stays 12 wide.

→ Recommend **(a)**. Bump `rainData[12]` → `rainData[24]` and `timeLabels[12][20]` → `timeLabels[24][20]` in [Dashboard.ino:142-143](../Dashboard.ino). Update chart x-axis labels in [C_Display.ino](../C_Display.ino) accordingly.

**Unit mismatch.** Open-Meteo gives mm-per-15-min (typically 0.0–2.5). Buienradar gives mm/h (typically 0.0–10.0 for moderate rain). Chart thresholds at [C_Display.ino:641-643](../C_Display.ino) are 0.1 / 1.0 / 2.5 mm:

```cpp
const float heavyThreshold  = 2.5f;  // currently mm-per-15min
const float mediumThreshold = 1.0f;
const float lightThreshold  = 0.1f;
```

Translation: `mm-per-15min × 4 ≈ mm/h`. Semantic bands in mm/h: 0.4 / 4.0 / 10.0.

**Make the unit explicit**: rename thresholds to `lightMmh`, `mediumMmh`, `heavyMmh`. When Open-Meteo is the source (fallback), multiply by 4 at fetch time so the chart only ever sees mm/h. (Simpler than carrying a unit flag.)

---

## B10 — Attribution requirement (legal)

Buienradar TOS requires visible attribution on any UI using the free feed. Add to the footer line in [C_Display.ino](../C_Display.ino) (the section that draws "updated HH:MM" at y=590):

```
updated 19:15 · BR · data: Buienradar.nl
```

Attribution is required whenever any Buienradar value is on screen — Stage 1 already triggers the requirement. Footer character budget should be OK; current footer is short.

---

## B11 — Failure modes & edge cases

| Failure | Behavior |
|---|---|
| `data.buienradar.nl` returns 200 but malformed JSON | Log error, keep OM values |
| All stations stale (>60 min) | Log warning, keep OM values, mark footer `· OM` |
| Nearest station has iconcode but `temperature` missing | Skip station, try next |
| Buienradar HTTP timeout (8 s) | `httpGetWithRetry` handles 3 attempts; final failure → keep OM |
| Wind direction string is unrecognized | `bearingFromDutchCardinal` returns -1 → fall back to OM's windDirection |
| `iconcode` is a new letter we haven't mapped | Falls through to OVERCAST + DBG log |
| `raintext` returns 200 but body is 0 bytes | Keep OM rain |
| `raintext` lines malformed (e.g. only one line) | Use what parsed; if 0 valid lines, keep OM |
| Buienradar serves redirect to consumer page (rare past incident) | `http.GET()` won't follow; treat non-200 as failure → keep OM |

---

## B12 — Files modified

- **[B_Network.ino](../B_Network.ino)** — add `fetchBuienradarCurrent()` and (Stage 2) `fetchBuienradarRain()`. ~80 + ~30 lines.
- **[A_Calculations.ino](../A_Calculations.ino)** — add `categorizeBuienradarIcon()`, `bearingFromDutchCardinal()`, `haversineKm()`. ~50 lines.
- **[Dashboard.ino](../Dashboard.ino)** — wire Buienradar calls into `setup()` after Open-Meteo; (Stage 2) bump `rainData[12]→[24]`, `timeLabels[12]→[24]`.
- **[C_Display.ino](../C_Display.ino)** — footer: add source-tag (`· BR`/`· OM`) and Buienradar attribution; (Stage 2) widen rain chart to 24 bars, rebase thresholds to mm/h.
- **[config.h.example](../config.h.example)** — `BUIENRADAR_FEED_URL`, `BUIENRADAR_RAIN_URL`, `BUIENRADAR_STALE_MIN`.

No `icons.h` changes. No new fonts. No struct changes for Stage 1 (Option α). Stage 2 changes only the array sizes.

---

## Verification

**Stage 1 (current weather):**
- Watch serial log over 4–8 wakes (1–2 h): confirm nearest station picked, freshness OK, no fallbacks.
- Compare on-screen current condition vs Buienradar.nl in browser at same moment. Should agree.
- Force fallback by temporarily setting `BUIENRADAR_FEED_URL` to a bad URL; confirm OM values render and footer shows `· OM`.
- Trigger an unmapped iconcode by injecting a test value in `categorizeBuienradarIcon`; confirm OVERCAST renders and DBG logs the code.

**Stage 2 (rain):**
- During rain, compare 5-min nowcast bars to Buienradar.nl's app graph.
- During dry weather, confirm all bars are zero and the dry-state temp curve fallback path renders unchanged.
- Heap check: `DBG("Buienradar rain heap:")` should show >50 KB free after parse.

---

## Open questions worth raising before implementation

1. **Where to put the attribution?** Footer is least intrusive; alternative is small text under the current-weather icon (eats display real estate). Recommend footer.
2. **Should the footer source-tag (`· BR`/`· OM`) be permanent or removable after stability is proven?** Recommend keep — costs ~6 chars, makes silent fallbacks visible.
3. **Stage 1 → Stage 2 cadence: ship together or separately?** Recommend separate; user can override.
