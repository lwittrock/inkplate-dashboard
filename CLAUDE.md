# Inkplate 6 Dashboard — CLAUDE.md

## Project Overview

E-paper weather + train dashboard on an **Inkplate 6** (ESP32, 800×600 1-bit e-ink), mounted behind glass on the wall. Since the thin client (design: [docs/server-rendering-design.md](docs/server-rendering-design.md)) the work is split in two:

- **The server** (`server/`, Python, runs in CT 106 on the home server at `192.168.1.212`) fetches weather and trains, runs the train picker and the Buienradar vote, draws the whole 800×600 frame every 5 minutes, and decides when the device wakes next.
- **The firmware** (`*.ino`, about 300 lines) wakes, connects Wi-Fi, makes **one plain-HTTP request** on the LAN, draws the frame it gets, and deep-sleeps for as long as the reply says. The request reports battery, firmware and Wi-Fi signal, which the server forwards to Home Assistant and healthchecks.io.

**Location:** Delft / Den Haag, Netherlands. **Trains:** Den Haag Centraal (GVC) and Den Haag HS (GV) to Tilburg Universiteit (TBU); see "Train picker policy" below.

**Design source of truth:** the rendered frame, drawn by [server/screen/render.py](server/screen/render.py), whose band comments are the layout contract. `python -m screen.preview` in `server/` renders it on the laptop.

**The home server side** (CT 106's build, its firewall, the HA sensors, the checks) is documented in the `homeserver-docs` repo: `runbooks/inkplate-screen.md`, Phase 10 in its `plan.md`.

---

## Architecture

**Firmware** (all `.ino` files compile together):

```
Dashboard.ino   — setup(): the whole wake, the failure path, sleep
B_Network.ino   — connectWifi() (static IP + DHCP fallback), fetchScreen()
C_Display.ino   — drawGrey() / drawFrame() (the server's frame), drawMessage() ("Server down" / "No Wi-Fi")
D_OTA.ino       — manifest check, update, app-level rollback, the OTA triggers
Fonts/Inter_Bold18pt7b.h — the only font left, for the failure message
```

**One wake:**
```
setup()
  ├─ display.begin(), OTA RTC state, checkBootAttempts()   (may roll back)
  ├─ readBattery()                         before the radio is on
  ├─ connectWifi()          ── fails → failedWake(FAIL_WIFI)
  ├─ markFirmwareValid()                   "Wi-Fi came up" = healthy
  ├─ GET /v1/screen?batt=..&fw=..&rssi=..&wake=..&fail=..&awake_ms=..&wifi_ms=..
  │        ── fails → failedWake(FAIL_SERVER)   (OTA check first if due)
  ├─ checkForUpdates() if X-Ota: 1 or the device's own trigger
  ├─ Wi-Fi off, CPU to 80 MHz
  ├─ 200: greyscale (g4z): full refresh in 3-bit mode; 1-bit (m1z): drawFrame(), X-Refresh
  │  204: leave the panel as it is (night)
  └─ deep sleep X-Sleep seconds (clamped 300..28800; default 1800)
```

No NTP, no clock, no night mode, no cadence rules on the device: the server's schedule arrives in `X-Sleep`. The contract (query string, 60,000-byte frame, headers, 204) is in the design doc, "The contract". **Change it only backward-compatibly:** the server deploys within minutes of a push, the device only after midnight.

**Failure path** (`failedWake`): the first failure changes nothing on the panel (e-ink keeps its picture) and retries in 10 minutes; from the second, the panel says "No Wi-Fi" or "Server down" (drawn once, full refresh); retries back off to 30, then 60 minutes. The server answers a device reporting `fail > 0` with a full redraw, even at night.

**Server** (`server/`, see [server/README.md](server/README.md)): `screen/sources.py` (the APIs), `collect.py` (per-source caching), `trains.py`, `weather.py`, `headline.py`, `render.py` + `gfx.py` (draws exactly as Adafruit GFX did), `schedule.py` (wake cadence), `telemetry.py`, `service.py` (HTTP on 8088). Deploys itself from `master` when `server/` changes, after a self-test.

---

## Configuration

**Firmware:** `config.h` (gitignored; template `config.h.example`) needs **no field at all**: every setting has a default in code. Optional: `WIFI_STATIC_IP`/`GATEWAY`/`SUBNET`/`DNS` (production uses `.220`), `SCREEN_URL` (default `http://192.168.1.212:8088/v1/screen`), `DEBUG_LOG`, `OTA_MANIFEST_URL`, `OTA_TEST_FORCE_CHECK`, and `OTA_SKIP` for bench flashes only. An old `config.h` with the former fields (location, stations, cadence, night mode) still compiles; they are ignored. `secrets.h` (gitignored, read-denied for Claude): `WIFI_SSID`, `WIFI_PASSWORD`. A leftover `NS_API_KEY` is harmless.

**Server:** environment variables, from `/etc/inkplate-screen.env` in CT 106 or `server/local.env` on the laptop (template `server/local.env.example`): `NS_API_KEY`, `LATITUDE`, `LONGITUDE`, `HA_WEBHOOK_URL`, `HC_PING_*`, and the rest listed there.

---

## Upstream APIs (all called by the server now)

| API | Auth | URL |
|---|---|---|
| Open-Meteo (hourly 24h + daily 7d forecast) | None | `api.open-meteo.com/v1/forecast` |
| Buienradar feed (live KNMI station observations) | None | `data.buienradar.nl/2.0/feed/json` |
| Buienradar raintext (2h precipitation nowcast) | None | `gpsgadget.buienradar.nl/data/raintext?lat=…&lon=…` |
| NS Trip Planner v3 (GVC→TBU and GV→TBU) | `Ocp-Apim-Subscription-Key` header | `gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips` |

Refresh times and how long a failed source falls back to its last good copy: `POLICY` in [server/screen/collect.py](server/screen/collect.py). The HS trips keep the firmware's conditional fetch: every 45 minutes while Centraal runs clean, fresh the moment it shows a disruption.

**Train picker policy (`pick_departures` in [server/screen/trains.py](server/screen/trains.py)):** for each of 3 slots take the next Centraal trip. If it's *good* (not cancelled, delay < 10 min) → use as-is. If it's *bad* → look for a *clean HS substitute*; if none exists, show the disrupted CTR with its `cancelled` / `+Xm late` status visible (the user wants to see the disruption, not have it hidden). A clean HS substitute satisfies **all** of: not cancelled, `leg_count ≤ 2` (rejects via-Rotterdam multi-transfer ghost routings that NS Trip Planner returns when queried `GV→TBU` directly), departs within ±10 min of the bad CTR's planned time, departs ≥ now + 5 min (reachable on foot), and arrives strictly earlier than every already-filled slot (catching the same Breda→TBU sprinter as a slot already shown adds zero info). On substitution no note text is written: the HS visual treatment (filled black "DH HS" pill + 2 px outline) is the origin signal. When CTR returns 0 trips (Trip Planner outage), HS is promoted to primary with the same filters. Trains that have left are dropped (a delayed one counts until it actually leaves), and trains more than 3 hours ahead are too (`LOOKAHEAD`), so after the last train of the evening the card slots stay empty instead of showing tomorrow's. **All comparisons use full timestamps:** the firmware compared "HH:MM" text, and a train arriving after midnight "beat" every earlier one (found 23 September 2026). The ±10 min window is intentionally tight; widen `SUBSTITUTE_WINDOW` if real disruption data shows clean HS alternatives rejected for ±12–15 min lag. The tests in `server/tests/test_trains.py` follow this paragraph rule by rule.

**Buienradar consensus picker (TO REVISIT), `pick_current` in [server/screen/weather.py](server/screen/weather.py):** not just the nearest station, because that was brittle to single-sensor outliers (observed 2026-05-25: Voorschoten at 9 km reported OVERCAST while Rotterdam, Hoek van Holland, Schiphol and Lopik all within 50 km reported CLEAR, and a blue-sky day rendered with a cloud icon). It takes the `BUIENRADAR_MAX_CANDIDATES` nearest fresh stations, votes the mode of the weather category across those within `BUIENRADAR_CONSENSUS_KM` (default 30 km, ties broken by proximity), and sources temp/wind/icon from the closest station that voted for the winning category. Known caveats: (1) AWS-class stations like Voorschoten and Rotterdam Geulhaven use cheaper optical sensors than the KNMI synoptic stations and probably deserve less weight; (2) mode-blending lags real frontal passages by one render; (3) categorical ties on transitional days can flicker. A "trust the synoptic stations, ignore AWS-only" rule may beat voting once there is data.

---

## Display Layout (800×600px, 1-bit), drawn by the server

```
y=0    ┌─ MASTHEAD: greeting + date + sun/moon arc with current dot ───┐
y=92   ├─ thick rule (2 px) ──────────────────────────────────────────┤
y=112  │ WEATHER  |   RAIN COMING / NEXT HOURS DRY                   │
y=125  │ 128px icon  │  axes + rain chart (Bayer fill) OR            │
       │ big temp °  │  24h temp curve with sunrise/sunset guides    │
y=232  │ wind arrow + "X km/h"                                       │
y=305  ├─ dotted divider ─────────────────────────────────────────────┤
y=324  │ WEEK — 7 cells × 102 px, day name + 48 icon + range bar     │
y=455  ├─ dotted divider ─────────────────────────────────────────────┤
y=474  │ TRAINS → BREDA — 3 cards × 220 px (CTR or HS pill)          │
y=590  └─ FOOTER: updated HH:MM + battery icon ──────────────────────┘
```

All Y coordinates are absolute; the section comments in `render.py` annotate each band. The fonts (Inter, OFL) and icons were imported from the firmware's GFX headers and `icons.h` into `server/screen/assets/`, and `server/screen/gfx.py` reproduces Adafruit GFX's integer drawing routines, so the port matched the old wall screen pixel for pixel. New sizes for a redesign can come from TTF alongside. **Sizing:** GFX point sizes don't map 1:1 to CSS pixels: `9pt7b` cap-height ≈ 14 px, `12pt7b` ≈ 17, `18pt7b` ≈ 26, `48pt7b` ≈ 64. Glyphs outside ASCII (°, ·, →) are drawn as primitives.

---

## Build & Flash (firmware)

**IDE:** Arduino IDE 2.x, board "Soldered Inkplate6" (= FQBN `Inkplate_Boards:esp32:Inkplate6V2`), partition scheme `min_spiffs`. **Library:** `Inkplate` (Soldered, 11.1.0). The firmware no longer uses ArduinoJson; CI still installs it, which is harmless. The IDE bundles `arduino-cli` (`resources/app/lib/backend/resources/arduino-cli.exe` under the IDE's install folder), which compiles the sketch from a terminal.

**Releases come from CI, not from your IDE** (see "OTA gotchas"). A USB flash is for bench tests only, and a bench build must set `#define OTA_SKIP 1` in the local `config.h`: otherwise the "dev" build replaces itself with the newest release on its first successful wake. `#define BENCH_MAX_SLEEP_S 60` caps each sleep so a bench session doesn't wait half an hour per wake. CI refuses to build if the `CONFIG_H` secret defines either. The checklist for the switch-over is [docs/thin-client-switchover.md](docs/thin-client-switchover.md).

**Server:** `cd server && python -m pytest` (on this laptop set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), `python -m screen.preview`, `python -m screen.service`. See [server/README.md](server/README.md).

---

## Known Issues & Limitations

- `config.h` and `secrets.h` must NOT be committed.
- No TLS certificate validation (`setInsecure()`) for the OTA manifest and binary; the screen request is plain HTTP on the LAN by design (TLS handshakes were the battery's main cost; the frame and the report are not secret).
- The server listens to the whole LAN on 8088, so any LAN device could post a fake battery report. Accepted.

---

## Working with Claude Code

- **Boot-path discipline — the whole firmware is boot path now.** The device is behind glass. Any change that stops a wake from reaching `markFirmwareValid()` triggers app-level rollback at best and needs a physical reflash at worst. Every firmware change gets a USB bench test (with `OTA_SKIP`) before it goes to `master`. Layout and logic changes belong in `server/`, which deploys without touching the device.
- **Pushing to `master`:** firmware changes (`*.ino`, `*.h`, `Fonts/**`) build a release the device installs after midnight; `server/` changes are live on CT 106 within ~5 minutes if `screen.selftest` passes, and `server-tests.yml` runs the full server tests on GitHub. The release workflow excludes `server/**` explicitly. A commit touching both deploys twice, at different times: the contract must stay backward compatible.
- **RTC state needs a magic sentinel and a layout-bump discipline.** `RTC_DATA_ATTR` variables survive deep sleep but not a power loss or an OTA reboot. `D_OTA.ino` guards its state with `OTA_RTC_MAGIC` (now `0xC0FFEE47`; `42` was the old layout, `44`–`46` the old caches: don't reuse). **If you change the layout of the OTA RTC state, bump the magic in the same commit.** The thin client's other RTC values (`wakeCounter`, `failStreak`, `shownMessage`, `prevAwakeMs`, `prevWifiMs`) are safe at zero and need none.
- **Frames and memory:** the device asks for `fmt=g4z,m1z` and draws whichever the server sends (`X-Format`, the service's `SCREEN_FORMAT`). Both arrive zlib-compressed (~14 KB greyscale, ~7 KB 1-bit) and are inflated by the ESP32 ROM's `tinfl_decompress` (`esp32/rom/miniz.h`), its ~11 KB state on the heap because `setup()` has an 8 KB stack. A greyscale frame inflates straight into the library's 3-bit buffer, `display.DMemory4Bit` (the wire format is that buffer's layout); a 1-bit one into a 60 KB `ps_malloc`'d buffer (static RAM overflows `.dram0.bss`). `display.selectDisplayMode()` switches between `INKPLATE_3BIT` and `INKPLATE_1BIT` per frame; the failure message always draws in 1-bit. Prefer `char buf[N]` + `snprintf` over `String` on the heap.
- **1-bit display, and why every refresh is full.** `display.display()` is a full refresh (~1–2 s, flashes). **`partialUpdate()` after deep sleep is a full refresh too** (found 23 September 2026): the library (11.1.0, `Inkplate6Driver.cpp`) sets `_blockPartial` on every boot and clears it only after a full refresh, and every wake is a boot. So the server's `X-Refresh: partial` has no effect today, and never did in the old firmware either; the panel has never ghosted. A real partial refresh needs `partialUpdate(true)` with the previous frame loaded first: a lever to decide with measured data (design doc, "Later").
- **Pixel-accurate layout** lives in `server/screen/render.py`: treat its band comments as the layout contract, and check `python -m screen.preview` output before pushing.

---

## Non-obvious gotchas (spike findings worth preserving)

These were discovered during integration spikes and are not derivable from the code alone. Keeping them so a future change doesn't re-discover them the hard way.

**NS Trip Planner v3** (read by `server/screen/sources.py`):
- The existing Reisinformatie v2 API key works for v3 — no separate portal subscription needed. No rate limit shows in the portal; the server makes at most ~200 trip calls a day.
- Den Haag HS is `GV`, not `GVH`. `GVH` returns HTTP 400.
- **GVC→TBU trips are normally 2 legs** (IC to Breda, SPR Breda → TBU). A 3-leg night routing (04:44) was seen on 23 September 2026.
- **GV→TBU trips are 2 legs when clean (IC Direct via Rotterdam to Breda + SPR to TBU) but Trip Planner also returns 3+ leg via-Rotterdam-with-Sprinter-changes routings** that NS doesn't surface in the consumer app. Hence the `leg_count ≤ 2` filter on substitutes.
- Payloads are ~90 KB (GVC→TBU) and ~115 KB (GV→TBU). This drove the firmware's slurp-and-filter parsing; on the server it no longer matters.
- NS returns multiple "routing options" for the same physical train (identical planned departure). The picker dedups by planned departure.
- ISO timestamps come with offsets *without* a colon (`+0200`); Python's `fromisoformat` accepts them. The server converts every time to Amsterdam wall-clock.

**Buienradar feed schema:**
- The weather icon is exposed as an *image URL* (`.../weather/30x30/aa.png`) — there is no `iconcode` field. Filename stem = code.
- Icon codes are doubled letters for the day variant (`aa`, `bb`) and single for the night variant (`a`, `b`); doubled letters collapse to single before lookup. `cc` is the only multi-char code that stays distinct.
- `winddirectiondegrees` is an integer (0–360); `winddirection` (Dutch cardinal string) is only the fallback.
- `feeltemperature` is lowercase. Not used.

**Open-Meteo:** `precipitation_hours` arrives as a JSON float (`12.0`). The firmware read it with ArduinoJson's `| 0`, which returns the default for anything not stored as an integer, so its "≥ 3 hours of precipitation means drizzle" rule never fired. The server reads the number. (Also found on 23 September 2026: the firmware's "Wet and windy" headline could never appear, because "Windy" always claimed the slot first.)

**Home network topology (as of 2026-07-26 router swap):**
- **The TP-Link Deco is the access point; a DrayTek Vigor is the router.** The Deco runs in AP/bridge mode behind the DrayTek, which owns DHCP on `192.168.1.0/24` (pool `.10`–`.209`). **SSID, password, band and WPA mode never changed** in the swap — same Deco radios — so `secrets.h` was untouched. Any address reservation must be made on the DrayTek (`LAN` → `Bind IP to MAC`); reservations in the Deco app do nothing in AP mode.
- Device static IP is `192.168.1.220`, bound to MAC `10:97:BD:DA:4A:F4`, deliberately **outside** the DHCP pool. The screen service is CT 106 at `192.168.1.212`.
- **Don't validate a candidate static IP against the router's DHCP table alone.** `.200` was the first pick: it answered a ping, then went silent, and the DrayTek's table showed it free — an intermittent device held the lease and was away when the table was read. Pick from outside the pool and bind the MAC.
- **The Inkplate is unreachable by ping ~98% of the time** (deep sleep, Wi-Fi off most of the time). A failed ping proves nothing. HA's `sensor.inkplate_last_seen` and the screen service's `/status` show when it last asked.

**CI / arduino-cli build gotchas:**
- **FQBN for Inkplate6 is `Inkplate6V2`, NOT `Inkplate6`.** The Soldered "Inkplate_Boards:esp32" package contains both. The legacy `Inkplate6` entry has `build.board=ESP32_DEV` which fails to define the `ARDUINO_INKPLATE6` macro that the v11+ Inkplate library's `driverSelect.h` requires — compile dies with `#error "Board not selected!"`. The IDE's "Soldered Inkplate6" picker silently selects `Inkplate6V2`. Lost ~1 hour to this in Phase 1.
- **arduino-cli requires the sketch folder name to match the main `.ino` filename.** CI copies `*.ino *.h` and `Fonts/` into `sketch/Dashboard/` before compiling. The local checkout's folder is already called `Dashboard`.
- **`config.h` and `secrets.h` are gitignored, but CI owns them as the source of truth**: whole-file Actions secrets `CONFIG_H` and `SECRETS_H`, written to disk before compile. Never reinterpret the semantics of an existing `config.h` field without renaming it.
- **`FIRMWARE_VERSION` is injected via a CI-generated `firmware_version.h`** (gitignored). Local builds use the `"dev"` fallback in `Dashboard.ino`.

**OTA gotchas (confirmed during Phase 3 deployment):**
- **Releases are AUTOMATIC on every push to `master` that touches firmware sources** (`*.ino`, `*.h`, `Fonts/**`). The workflow computes the next tag as `v<YYYY.MM.DD>-NN` (UTC), creates the tag, builds, and publishes the release + updates the `firmware-latest` manifest. Docs, workflow and `server/` commits are filtered out by the paths list. To opt out of a single release, include `[skip release]` in the commit message. To force a rebuild without a code change, use workflow_dispatch.
- **Once OTA is live, CI's `CONFIG_H` secret IS the device's config.** Editing local `config.h` does nothing for the device; it only matters for USB flashes. To change production config: update the secret, then push (or workflow_dispatch).
- **Secret-before-push sequencing matters.** GitHub Actions binds secret values at step start time. Update the secret FIRST, then push.
- **Renaming or removing a `config.h` field — exact workflow.** (1) Update both `config.h.example` and the local `config.h`. (2) Update the `CONFIG_H` secret. (3) Only then push the code change. (4) Verify the release built green before walking away. The thin client avoids new required fields altogether by giving each a default in code.
- **RTC RAM does NOT survive OTA-induced reboot on this hardware. Accepted limitation.** The OTA path (`httpUpdate.update()` → SW_CPU_RESET) clears RTC slow memory on the Inkplate6V2: `wakeCounter` resets to 0, `otaRtcMagic` mismatches → `initOtaState()` resets everything including `otaPendingVersion`. **Failure mode it leaves open:** if a newly-OTA'd firmware crashes on its very first boot (before reaching `markFirmwareValid()`), `pendingVersion` is already gone, so rollback never identifies "this version is bad" and the device is stuck. Recovery is USB reflash. Untried fix candidates if this ever bites: `RTC_NOINIT_ATTR` or NVS-backed persistence.
- **`markFirmwareValid()` runs as soon as Wi-Fi is up, before the server request.** A server outage must not count against new firmware and roll it back. A release that breaks only the server request is caught by the OTA triggers while failing (below) instead.
- **OTA must never depend on the server.** Triggers: the server's `X-Ota: 1` (sent on its 00:05 wake, so updates land while the house sleeps and soak ~6 hours before morning); the device's own counter (48 hours of summed sleep without a check); and while the server is unreachable, the second failed wake and then every 12 hours of sleep. A cold boot (and an OTA reboot, which clears RTC) checks once.
- **"dev" lexical compare exception is required for local-build → OTA upgrade path.** `FIRMWARE_VERSION="dev"` lexically sorts > all digit-starting versions. Without the `localIsDev` exception in `checkForUpdates()`, a USB-flashed local build could never OTA-pull a tagged release. Don't remove it.
- **GitHub release asset URLs 302-redirect** to `objects.githubusercontent.com`. `httpUpdate.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS)` is required. Without it the download returns 0 bytes silently.
- **A USB-flashed local build replaces itself on its first successful wake unless `OTA_SKIP` is set.** Consequence of the "dev" exception and the cold-boot check: the local build connects, checks the manifest, and OTAs itself to the newest release. Observed 2026-07-26 with the old firmware (it pulled a two-month-old release with the old subnet). **Bench builds set `OTA_SKIP`**; to get a local change onto the wall, push to `master` and let OTA deliver it.
- **Serial output between USB upload and serial monitor reconnect is lost.** Don't conclude "the firmware didn't reboot" just because you didn't see the first lines.

**OTA design decisions (the WHYs):**
- **Not per wake.** Per-wake manifest checks would add ~0.03 mAh per wake; once a night (server hint) plus a 48-hour fallback costs next to nothing.
- **Plain text manifest, not JSON.** Two lines: version, then binary URL.
- **Manifest hosted on `firmware-latest` orphan branch, not GitHub Pages.** Manifest URL: `raw.githubusercontent.com/.../firmware-latest/version.txt`. The binary stays in releases.
- **App-level rollback, NOT bootloader rollback.** Stock Arduino-ESP32 doesn't enable `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`. An `RTC_DATA_ATTR` boot-attempts counter + `esp_ota_set_boot_partition()` gives the same outcome.
- **3 boot-failure threshold.** Tolerates one transient failure before rolling back.
- **`min_spiffs` partition.** Two 1.9 MB OTA slots; the thin client is ~1.1 MB.
- **No SHA256 verification of downloaded binary.** Personal device + own GitHub releases.
- **Whole-file `CONFIG_H` / `SECRETS_H` Actions secrets**, not field-by-field.

**OTA out of scope (deliberately not doing — don't re-evaluate without a reason):**
- Signed firmware updates, delta updates, staged environments / canaries (one device, one user)
- TLS certificate validation (`setInsecure()`, consistent with project posture)
- Custom bootloader with `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`
- A manual "skip OTA" recovery path via the WAKE button (frame is fully enclosed)
- **Changed 23 September 2026:** "remote logging back to a server" and "no battery monitoring code" used to be listed here. The thin client reports battery, firmware, Wi-Fi signal and per-wake timings on every request, and Home Assistant keeps them (low-battery warning below 15%). That is status reporting, not logging: debugging still happens over USB serial.

**Wake cadence (`server/screen/schedule.py`):** Wi-Fi-active time was ~92% of the old daily budget, so wake count was the largest lever. Weekday commute windows (06:30–09:30, 16:00–19:30) every 15 minutes, the rest of the weekday every 30, weekends every 15 all day (no fixed commute; the panel is read at unpredictable times), nothing 23:30–06:30 except the 00:05 OTA wake. Each wake lands 30 s after a 5-minute render slot, which also corrects the ESP32 sleep timer's drift. Sleep lengths are real seconds across DST. Moved from the firmware unchanged; revisit only with measured battery data (HA's awake and Wi-Fi time sensors).

**Battery optimizations consciously skipped:**
- TLS cert pinning — small power win, big code/maintenance cost.
- CPU clock below 80 MHz — causes Wi-Fi instability.
- Region-targeted partial refresh — ghosting risk with Bayer-dithered fills is too high.
- Full refresh every 8th wake instead of every 4th — evaluated 2026-07-26 and rejected (~0.3 mAh/day against ghosting on the Bayer-dithered rain chart). Moot, as it turned out: every refresh is full (see the 1-bit display note). `FULL_REFRESH_EVERY` lives on as a server constant for a future real partial refresh.
- Sleep current optimization — ~30–40 µA is already near the floor for Inkplate 6's onboard regulators.
- **Next lever, only if HA's `wifi_ms` sensor shows it matters:** keep the access point's BSSID and channel in RTC and pass them to `WiFi.begin()` to skip the scan.
