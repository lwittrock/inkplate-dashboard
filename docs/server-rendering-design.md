# Design: the home server draws the screen

Written 23 September 2026, from a planning session that followed
[`homeserver-integration-handoff.md`](homeserver-integration-handoff.md). This is option D from
that handoff, and it absorbs options A, B and C.

**What this document owns, and what it doesn't.** It owns **the contract** between device and
service (kept current: change it here first) and **the reasons** for the design. It is otherwise
the record of the design as decided, not a manual:

| For | Read |
|---|---|
| How the server code works, running it on the laptop, how it deploys | [`server/README.md`](../server/README.md) |
| The firmware, its rules and gotchas | [`CLAUDE.md`](../CLAUDE.md) |
| Building and operating CT 106, the HA sensors, the checks | `runbooks/inkplate-screen.md` in the home-server repo |
| Bench test and release of the thin client | [`thin-client-switchover.md`](thin-client-switchover.md) |
| Battery figures, modelled and (later) measured | [`power-audit.md`](power-audit.md) |

## In one paragraph

A small service on the home server fetches the weather and trains, runs the train picker and the
Buienradar vote, and draws the 800x600 1-bit screen every few minutes. The Inkplate wakes, makes
one plain-HTTP request on the LAN, receives the finished frame, shows it, and sleeps for as long as
the reply tells it to. The same request carries the device's battery, firmware and signal, which
the service passes to Home Assistant and healthchecks.io. The firmware shrinks from about 3,100
lines to about 300 and should rarely change again; layout and logic changes become server deploys
that never touch the device behind glass.

## Decisions (23 September 2026)

| Question | Decision | Why |
|---|---|---|
| Data only (D1) or finished picture (D2)? | **D2, the finished picture** | Same battery gain as D1, but layout and logic changes stop needing firmware releases |
| Priority | **Battery** | Every choice below is judged by radio time first |
| A, B, C | **Folded into D2, done by the server** | Zero device cost; see "Battery" |
| Keep the old direct-fetch path as fallback? | **No. Remove all legacy code** | Simplicity; a fallback would keep about 2,800 lines alive and drift from the server's layout |
| When the server is unreachable | **Show "Server down" (or "No Wi-Fi"), nothing else** | With the three refinements under "The device" |
| Starting design | **Port today's screen first**, improve afterwards | Separates "did the port work" from "do I like the new design" |
| Where it runs | **New LXC, CT 106, 192.168.1.212** | See "The container" |
| How code reaches it | **The container pulls from GitHub by itself** | Cleanest loop for design iteration; see "Deploying" |
| Where the code lives | **This repo, under `server/`** | Next to the firmware whose contract it must match |

## Architecture

```
 CT 106 inkplate-screen (.212)                                   internet
 +--------------------------------------------+   HTTPS    +------------------+
 | render loop, every 5 min                   | ---------> | Open-Meteo       |
 |   fetch sources (per-source cache + TTL)   |            | Buienradar x2    |
 |   picker, consensus vote, headlines        |            | NS Trip Planner  |
 |   draw 800x600 1-bit -> frame in memory    |            | healthchecks.io  |
 |                                            |            +------------------+
 | HTTP :8088                                 |
 |   GET /v1/screen?batt=..&fw=..  <----------+-------- Inkplate .220 (one request per wake)
 |     reply: 60,000 bytes + X-Sleep, ...     |
 |     then, after replying:                  |
 |       POST telemetry -> HA webhook  -------+------> VM 100 Home Assistant .18:80
 |       ping healthchecks `inkplate`         |
 |   GET /preview.png  <----------------------+-------- laptop (management set)
 +--------------------------------------------+
```

## The contract

This is the only thing the firmware and the service share. Change it only in a
backward-compatible way: the service deploys within minutes of a push, the device only after
midnight, so for a night both versions of the other side must work. A breaking change gets a new
path (`/v2/screen`) and the service serves both until the device has moved.

**Request:** `GET http://192.168.1.212:8088/v1/screen?fmt=g4z,m1z&batt=3.91&fw=v2026.10.02-01&rssi=-61&wake=812&fail=0&awake_ms=2140&wifi_ms=1310`

| Parameter | Meaning |
|---|---|
| `fmt` | The frame formats the device can draw, comma-separated (below). The service picks one |
| `batt` | Battery voltage, two decimals, from `display.readBattery()` |
| `fw` | `FIRMWARE_VERSION` |
| `rssi` | `WiFi.RSSI()` |
| `wake` | Wake counter since the last cold boot or OTA reboot |
| `fail` | Consecutive failed wakes before this one (0 normally) |
| `awake_ms`, `wifi_ms` | Previous wake's total active time and Wi-Fi connect time, kept in RTC. This is what turns the battery model into measurements |

**Frame formats** (added 24 September 2026; the service's `SCREEN_FORMAT` setting, `grey` by
default, decides between them; [server/screen/frames.py](../server/screen/frames.py)):

| `X-Format` | Frame | Decompressed | Typical on the wire |
|---|---|---|---|
| `g4z` | greyscale: 4 bits per pixel, two per byte, left pixel in the high nibble, levels 0 (black) to 7 (white). Exactly the Inkplate library's 3-bit buffer, `DMemory4Bit` | 240,000 bytes | ~14 KB |
| `m1z` | 1-bit: 600 rows of 100 bytes, MSB first, 1 = black | 60,000 bytes | ~7 KB |
| (none) | a request without `fmt`: the same 1-bit frame, raw, the contract's first form | | 60,000 bytes |

`g4z` and `m1z` are zlib streams (header and Adler-32 checksum), which the ESP32's ROM decompresses
(`tinfl_decompress`). The device checks the decompressed length is exact.

**Reply, success:** status 200, the frame in the format named by `X-Format`.

| Header | Meaning | Device handling |
|---|---|---|
| `X-Sleep` | Seconds until the next wake | Clamped to 300..28,800. Missing or invalid: 1,800 |
| `X-Format` | `g4z` or `m1z` | Selects the panel mode: greyscale is always a full refresh |
| `X-Refresh` | `full` or `partial` | 1-bit only. Today every refresh is full: see below |
| `X-Ota` | `1` = check the OTA manifest now | Optional hint; the device has its own trigger too |

**Why greyscale costs about the same as 1-bit** (worked out 24 September 2026 from the library
code): both refreshes start with the same 77-scan cleaning run, then 1-bit draws in 6 scans and
greyscale in 9, so the greyscale refresh is about 4% longer. The compressed greyscale frame (~14 KB)
is smaller than the raw 1-bit frame the first contract sent (60 KB), so radio time goes down. What
greyscale rules out is a real partial refresh, which the panel only does in 1-bit: switching back is
`SCREEN_FORMAT=mono` on the service, no firmware change.

**Reply, keep the panel:** status 204, no body, with `X-Sleep` and possibly `X-Ota`. A success: the
device resets its failure count, skips drawing, and sleeps. The service sends it at night (23:30
to 06:30), so the 00:05 OTA wake does not replace the evening's screen with a midnight one. A
device reporting `fail > 0` gets a 200 even at night, so a "Server down" screen clears at once.
(Added 23 September while building step 2.)

**Anything else is a failure:** another status, an unknown `X-Format`, a body that does not
decompress to exactly the format's size, a connect over 2 s, or a total over 5 s. A damaged frame
must never reach the panel.

## The device

### Wake flow

```
setup()
  ├─ display.begin(), battery reading
  ├─ OTA rollback bookkeeping (as today)
  ├─ Wi-Fi connect (static IP, DHCP fallback as today)
  │    └─ failed → failure path ("No Wi-Fi")
  ├─ markFirmwareValid()                 ← "Wi-Fi came up" is the new health criterion
  ├─ GET /v1/screen
  │    └─ failed → failure path ("Server down")
  ├─ OTA check if X-Ota: 1 or the device's own trigger fires
  ├─ 200: drawBitmap(frame), full or partial refresh per X-Refresh
  │  204: leave the panel as it is
  └─ deep sleep X-Sleep seconds
```

No NTP, no clock, no night mode, no cadence rules: the device does not need to know the time. The
server's schedule arrives in `X-Sleep`.

### Failure behaviour

1. **Two failures in a row before the message.** On the first failure the device changes
   nothing on the panel (e-ink keeps the last picture without power) and retries in 10 minutes.
   On the second it draws the message with a full refresh. It does not redraw it on later failures.
2. **"No Wi-Fi" or "Server down"**, depending on which step failed. Centered, one font, nothing
   else.
3. **Back off:** after the message, retry every 30 minutes, then every 60 minutes from the fourth
   failure. Without the server the device does not know when it is night; hourly retries through
   a night cost about 0.3 mAh.

Recovery needs nothing special: the next successful request draws the screen with a full refresh
(the service sees `fail > 0` and sends `X-Refresh: full`).

### OTA must never depend on the server

A release that breaks the server request could otherwise never be replaced over the air, which
means taking the device off the wall. So:

- **Primary trigger:** the server sends `X-Ota: 1` on one wake shortly after midnight (it
  schedules a wake around 00:05 for this, like today's night wake). Updates keep landing while
  the house sleeps.
- **Safety net, on the device:** check anyway when 48 hours of summed sleep have passed without a
  check, and on the second failure of a streak, then at most every 12 hours while it lasts. The
  sum lives in RTC; after an OTA reboot RTC is cleared, which makes the first boot check once.
  That is harmless.
- `markFirmwareValid()` moves to "Wi-Fi came up". A server outage must not count against new
  firmware; a bad release that breaks only the request is caught by the safety net instead.

### What stays and what goes

**Stays:** `D_OTA.ino` (trigger changed as above), Wi-Fi with static IP and DHCP fallback, battery
reading, deep sleep, one font for the failure message, the RTC boot-attempts logic.

**Goes, in the same release** (every `.ino` in the folder compiles, so they must be deleted, not
left unused): `A_Calculations.ino`, nearly all of `B_Network.ino` and `C_Display.ino`, `icons.h`,
six of the seven fonts, NTP, night mode, the adaptive cadence, `brCache`, `hsCache`, the Open-Meteo
daily cache.

**`config.h`:** new field `SCREEN_URL`. Most other fields become unused; remove them from
`config.h.example` and the `CONFIG_H` secret in the same pass. Follow the rename workflow in
`CLAUDE.md`: secret first, then push. **`secrets.h`:** `NS_API_KEY` moves to the server; only the
Wi-Fi details remain.

## The service

Python on Debian 13's own Python, with **Pillow as the only third-party dependency**, pinned by
hash. HTTP serving uses the standard library (`http.server.ThreadingHTTPServer`); there is one
client. Fewer dependencies means less that an automatic deploy pulls from PyPI.

### Rendering loop

- **Every 5 minutes**, on the clock (:00, :05, ...). Each source has its own cache and time-to-live,
  so a render only fetches what has expired:

  | Source | TTL | Notes |
  |---|---|---|
  | Open-Meteo hourly | 15 min | |
  | Open-Meteo daily | 6 h, or at the date change | As today |
  | Buienradar feed (now) | 10 min | Consensus vote as today |
  | Buienradar rain | 5 min | The nowcast is what makes freshness matter |
  | NS GVC→TBU | 5 min | |
  | NS GV→TBU | 5 min when GVC is disrupted, else 45 min | As today's conditional fetch |
  | healthchecks.io status (option C) | 5 min | Read-only key, separate from Homepage's |

- **At night (23:30 to 06:30)** the loop keeps running but fetches nothing, so the render check
  stays simple and the NS quota is untouched.
- **A failed fetch keeps the last good data.** Past a limit (say 30 minutes for trains and rain,
  3 hours for the rest) the section shows it has no current data instead of old values.
- The service renders once before it starts listening, so it never has "no frame yet".
- **NS rate limit:** checked 23 September, the NS API portal shows no limit for this
  subscription. At most about 200 trip calls a day (against about 70 today) is fine.

### Schedule policy (`X-Sleep`)

Today's cadence moves here unchanged: weekday peak windows 15 minutes, weekday off-peak 30,
weekends 15 all day, night 23:30 to 06:30 with the 00:05 OTA wake. Sleep is computed to land
about 30 seconds after the next render slot, so the device always gets a frame at most 30 seconds
old. The ESP32's sleep timer drifts by a few percent, but the service recomputes on every wake, so
the drift never accumulates. Battery comes first: the cadence is a candidate for lengthening once
real measurements exist, not before.

`X-Refresh`: `full` every fourth wake, after a failure streak, and on the first request from a new
firmware version; `partial` otherwise.

**Correction, 23 September 2026:** a partial refresh has never happened on the wall. The Inkplate
library (11.1.0, `Inkplate6Driver.cpp`) sets `_blockPartial` on every boot and turns
`partialUpdate()` into a full refresh until one full refresh has run; every wake is a boot after
deep sleep, so every refresh has been full, in the old firmware and in the thin client alike. The
header is kept because it costs nothing and a real partial refresh is a candidate lever (see
"Later"). It also means the panel has never ghosted, and that the battery model's e-ink line (a
quarter full, three quarters partial) is too low.

### What gets ported

The logic moves from C++ to Python with unit tests written from the rules in `CLAUDE.md`, which
are the specification:

- `pickDepartures`: the per-slot CTR/HS picker, the clean-substitute rules, the dedup by planned
  departure, the promotion of HS when CTR returns nothing.
- `fetchBuienradarNow`'s consensus vote, `categorizeBuienradarIcon`, the doubled-letter icon codes.
- The masthead's editorial headline picker, the sun/moon arc, the Bayer-dithered rain chart, the
  24-hour temperature curve, the week strip.
- Test fixtures: recorded API responses (including a disrupted NS day, if one can be caught) so
  renders are reproducible offline.

**Fonts and drawing.** For the exact port (23 September, step 1), the firmware's own GFX fonts and
icons were imported and Adafruit GFX's drawing routines reproduced, so the port could be checked
against the wall pixel for pixel; it matched. **Since 24 September** the screen is the redesign
(`server/screen/render.py`): Inter as a variable TTF at any size and Material Symbols weather
icons, drawn for the panel's greyscale and 1-bit modes. The exact port was removed then and is in
git history (commit `c9a36d2`).

**Laptop preview:** `python -m screen.preview` renders from live APIs or from fixtures into a PNG,
enlarged 2x for viewing. This is the design loop: change, preview in seconds, push.

### Telemetry (options A and B)

After sending the reply (never before: the device's radio is waiting), the service:

1. Stores the last request's values, with a timestamp, in a small JSON file.
2. POSTs them to the HA webhook, plus battery percentage from a LiPo voltage curve.
3. Pings the healthchecks.io check `inkplate`.

**In HA:** a trigger-based template sensor on the webhook gives `battery_v`, `battery_pct`, `rssi`,
`firmware`, `wake_ms` and `last_seen`. An automation notifies `mobile_app_lars_phone` below 15%, in
the style of the Proxmox alert. The webhook has `local_only: true`, and its id is a secret.

**The `inkplate` check** alerts when the device goes quiet. Worked out 23 September against
the schedule policy below:

- **Cron `*/30 7-22 * * *`, timezone Europe/Amsterdam, grace 1 hour.** healthchecks.io expects a
  ping by each tick plus grace, counting from the first tick after the last ping.
- **Daytime:** the device requests at least every 30 minutes (weekday off-peak), usually every 15.
  A device that goes quiet is reported within 1 to 1.5 hours. A short failure streak (the 10
  minute retry, then 30 minutes) stays inside the grace.
- **Night:** the last tick is 22:30, but pings continue until about 23:30, and the 00:05 OTA wake
  pings once more. The first tick after that is 07:00, due by 08:00. The device's first morning
  wake is 06:30. A normal night therefore never alerts, and a silent night is reported by 08:00.
- **Why not a simple period:** it would need a grace of more than seven hours to survive the
  night, so a daytime failure would go unnoticed for most of a day.
- **Daylight saving:** the check and the schedule policy both use Amsterdam local time, and no
  tick falls near the 02:00 to 03:00 switch.

Because the service does the pinging, a dead server also makes this check go quiet, alongside the
server's own checks. That's acceptable: either way the screen is not updating.

### Status line (option C): declined

Planned as "server OK" or "N problems" in the footer, from healthchecks.io's API. **Declined by
Lars on 24 September 2026:** not needed on the wall, since healthchecks.io and HA already alert
by themselves.

## The container: CT 106

A Debian 13 LXC at `192.168.1.212`: 1 core, 256 MB, 3 GB, no Docker, no SSH server; inbound only
port 8088 from the LAN, outbound to the LAN only Home Assistant's port 80. The service runs as
`screen`, sandboxed; secrets in root-only env files; no vzdump, because git and Proton Pass
rebuild it. Three healthchecks.io checks (9 of the free plan's 20): `screen-render`,
`screen-deploy`, `inkplate`. The build sheet and the operating notes are the home-server repo's
`runbooks/inkplate-screen.md`; why a container of its own, and the rest of the reasoning, its
`decisions.md`, Appendix H.

## Deploying: the container pulls

Every 5 minutes CT 106 fetches `master` of the public repo. When `server/` changed, it builds a
release with its own venv (Pillow pinned by hash), runs `python -m screen.selftest`, and only then
switches to it; otherwise the running release stays. Rollback is `git revert`. The deploy script
and units are installed by hand, so a push can change the service but not how it is deployed. Two
consequences that shape everything else:

- **Trust:** the server runs whatever reaches `master`, which only Lars can push to.
- **Two deploys from one branch:** a commit touching firmware and `server/` reaches the service
  in minutes and the device the next night. That is why the contract must stay backward
  compatible. The firmware's CI ignores `server/` explicitly (`!server/**`), and a separate
  workflow runs the server's unit tests on every change there.

Details: [`server/README.md`](../server/README.md), "On the server", and the runbook.

## Battery

**Model, per wake** (same method as `power-audit.md`, not measured):

| Phase | Today | New |
|---|---|---|
| Wi-Fi connect, static IP | ~180 mAs | ~180 mAs |
| Fetches (5 HTTPS, some cached) plus NTP | ~1,270 mAs | ~50 mAs (one LAN HTTP, 60 KB) |
| Render or draw, e-ink refresh, boot, sleep entry | ~145 mAs | ~90 mAs |
| **Per wake** | **~0.44 mAh** | **~0.09 mAh** |
| **Per day** (about 53 wakes plus sleep) | **~24 to 27 mAh** | **~5 to 8 mAh** |
| **Runtime, 960 mAh usable** | **~37 to 40 days** | **~4 to 6 months (model)** |

**Reality check.** Other people's server-rendered Inkplates report about 0.5 mAh per wake
(MagInkDash: 1500 mAh, hourly, projected 3 to 4 months; HomePlate: "1 month+"). None of those
figures are measured, and they involve grayscale refreshes, bigger panels and PNG decoding, which
this design avoids. Still, treat the model as optimistic. Honest expectation: **3 to 5 months**,
also because LiPo self-discharge starts to count at this low draw.

**Measured, not modelled, from the first week:** `awake_ms` and `wifi_ms` in every request give
real per-wake times, and the battery voltage in HA gives the real discharge curve. Recompute this
table from them and record the outcome in `power-audit.md`.

**Next lever, after measuring:** Wi-Fi connect becomes the largest cost. Keeping the access
point's BSSID and channel in RTC and passing them to `WiFi.begin()` skips the scan (reported
elsewhere as 3 s down to under 1 s), with a normal connect as fallback. Do it only if `wifi_ms`
shows it is worth it.

## Steps

Each step has a "Done when". Commands on the server are Lars's to run, one at a time.

1. **Renderer on the laptop.** Port, fixtures, unit tests, preview.
   *Done when:* a preview from live APIs matches the wall at the same moment, section by section,
   and the picker tests cover every rule in `CLAUDE.md`'s "Train picker policy".
   **Done 23 September 2026:** the preview matched the wall, including a firmware bug that left
   one train card in the evening. Three latent firmware bugs were then fixed on the server side
   only; `server/README.md` ("Relation to the firmware") lists them.
2. **Service on the laptop.** HTTP, schedule policy, telemetry forwarding (to a dummy endpoint).
   *Done when:* `curl` gets 60,000 bytes and sensible headers for a range of simulated times.
   **Done 23 September 2026:** `server/screen/service.py`; checked with live APIs and a dummy
   webhook, and at a simulated 00:05 (204, one OTA hint, sleep to 06:30:30). The schedule tests
   sweep every minute of a week and both clock changes.
3. **CT 106.** The home-server repo's `runbooks/inkplate-screen.md`, steps 0 to 12, after
   merging this branch into `master` (its Phase 10.1). **Prepared 23 September 2026:** the deploy
   script, the units, the self-test and hash-pinned requirements are in `server/`.
   *Done when:* a push to `server/` appears on the container by itself, `screen-render` is green
   for a week, and `/preview.png` opens from the laptop.
   **Built 24 September 2026:** a push (`bee6252`) deployed and restarted the service by itself,
   `/preview.png` opens from the laptop, and both checks were green an hour later. The week of
   green `screen-render` runs from then.
4. **Home Assistant.** The runbook's steps 13 to 17: seven sensors from the webhook (battery,
   voltage, signal, awake and Wi-Fi times, firmware, last seen) and a notification below 15%.
   *Done when:* made-up reports from the laptop show up in HA and a made-up 6% battery reaches the
   phone. The `inkplate` check is the runbook's step 18, at the switch-over in step 5: a check
   that was never pinged does not alert.
   **Done 24 September 2026:** three made-up reports reached the sensors, and the 6% one reached
   the phone.
5. **Thin firmware.** On the `thin-client` branch: written and compiled 23 September 2026
   (1,113,605 bytes; the old firmware was 1,251,349), never run on the device. The bench test and
   the release are a checklist of their own: [thin-client-switchover.md](thin-client-switchover.md).
   Two bench-only switches make it safe and quick, `OTA_SKIP` and `BENCH_MAX_SLEEP_S`; CI refuses
   to build a release with either. The `CONFIG_H` secret needs no change.
   *Done when:* the morning after the release, the wall shows the server's screen and HA shows
   telemetry.
6. **Clean up.** `CLAUDE.md` is rewritten for the thin client on the `thin-client` branch (the
   out-of-scope lines on remote logging and battery monitoring are changed there), and the handoff
   is marked superseded; `README.md` is rewritten with a map of which document owns which fact.
   Left for then: a status note in `power-audit.md` with the measured figures. In the home-server repo: `reference.md` (CT 106, secrets, checks),
   the new runbook, `runbooks/home-assistant.md`, `decisions.md`, `plan.md`.
7. **After a week or two of data:** recompute the battery table, then decide on the fast Wi-Fi
   reconnect and on the cadence. The battery gauge waits for the same data (decided 24 September
   2026): `_LIPO` in `telemetry.py` is a generic single-cell curve, off by perhaps 5 to 10 points,
   and each wake reports one ADC sample, where 20 mV of noise moves the flat middle of the curve
   by about 10 points. With one full discharge in HA's voltage sensor: fit the table to this cell,
   smooth the reading over the last few reports, and consider showing days left instead of a
   percentage. All server-side; the low-battery warning at 15% uses the same table. Until then
   the footer draws the icon without a number.

## Later, deliberately not now

- **Design improvements** beyond the port: after step 5, with the preview loop.
- **A real partial refresh:** a forced `partialUpdate(true)` diffs against the library's copy of
  the previous frame, which is empty after a boot. The server knows the frame it last served, so
  it could send that one as well (another 60 KB on the LAN, well under a tenth of a second of
  radio) and the device would load it as "previous" before drawing the new one. Gain: less panel
  energy per wake and no flashing. Risk: when the server's idea of the panel is wrong (a missed
  wake, a failure message), the diff leaves artefacts until the next full refresh. Decide with the
  measured `awake_ms` and battery data, alongside the Wi-Fi reconnect lever.
- **Grayscale:** the panel has a 3-bit mode, but it does not support partial refresh, so every
  wake would be a full, flashing refresh with more panel energy. Only with a reason.
- **Skipping unchanged frames:** the device sends a hash of its last frame, the service answers
  "unchanged", and the device skips download and refresh. That would save about 20% of a wake,
  but in the daytime the frame almost always changes (departures, the "updated" time).
- **A sun-with-showers icon.** Material Symbols has no sun-and-rain glyph, so days the forecast
  marks as sunny with showers show the plain rain icon in the week strip (the firmware's bitmap set
  had one). To solve later: compose one from `partly_cloudy_day` and `rainy`, or take a glyph from
  another open icon set.
- **"Buienradar and NS in HA"** from the home-server ideas list: the service already has the data
  and could expose it to HA as JSON.

## Sources consulted

- TRMNL's self-hosted server protocol, the same pull-a-picture pattern:
  <https://github.com/usetrmnl/byos>
- HomePlate, Inkplate firmware for server-drawn screens with telemetry over MQTT:
  <https://github.com/lanrat/homeplate>
- hass-lovelace-kindle-screensaver, battery in the image request's query string, forwarded to an
  HA webhook: <https://github.com/sibbl/hass-lovelace-kindle-screensaver>
- MagInkDash, server-rendered Inkplate 10 with projected battery life:
  <https://github.com/speedyg0nz/MagInkDash>
- ESP32 fast reconnect with BSSID and channel in RTC: <https://esp32.com/viewtopic.php?t=11306>
