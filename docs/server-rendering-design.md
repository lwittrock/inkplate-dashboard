# Design: the home server draws the screen

Written 23 September 2026, from a planning session that followed
[`homeserver-integration-handoff.md`](homeserver-integration-handoff.md). Nothing here is built yet.
This is option D from that handoff, and it absorbs options A, B and C.

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

**Request:** `GET http://192.168.1.212:8088/v1/screen?batt=3.91&fw=v2026.10.02-01&rssi=-61&wake=812&fail=0&awake_ms=2140&wifi_ms=1310`

| Parameter | Meaning |
|---|---|
| `batt` | Battery voltage, two decimals, from `display.readBattery()` |
| `fw` | `FIRMWARE_VERSION` |
| `rssi` | `WiFi.RSSI()` |
| `wake` | Wake counter since the last cold boot or OTA reboot |
| `fail` | Consecutive failed wakes before this one (0 normally) |
| `awake_ms`, `wifi_ms` | Previous wake's total active time and Wi-Fi connect time, kept in RTC. This is what turns the battery model into measurements |

**Reply, success:** status 200, body exactly **60,000 bytes**: 600 rows of 100 bytes, most
significant bit first, **1 = black**. This is the order Adafruit GFX's `drawBitmap` reads. Note that
Pillow's mode `"1"` uses 1 = white, so the service inverts before sending.

| Header | Meaning | Device handling |
|---|---|---|
| `X-Sleep` | Seconds until the next wake | Clamped to 300..28,800. Missing or invalid: 1,800 |
| `X-Refresh` | `full` or `partial` | Missing: `full` |
| `X-Ota` | `1` = check the OTA manifest now | Optional hint; the device has its own trigger too |

**Anything else is a failure:** another status, a body that is not exactly 60,000 bytes, a
connect over 2 s, or a total over 5 s. A short body must never reach the panel.

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
  ├─ drawBitmap(frame), full or partial refresh per X-Refresh
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

`X-Refresh`: `full` every fourth wake (as `FULL_REFRESH_EVERY` today), after a failure streak, and
on the first request from a new firmware version; `partial` otherwise.

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

**Fonts:** Inter TTF (OFL) in `server/fonts/`, drawn without anti-aliasing. Point sizes will not
map one to one to the GFX headers; match the cap heights noted in `CLAUDE.md` (14, 17, 26, 64 px).
**Icons:** a one-off script turns the bitmaps in `icons.h` into PNGs under `server/assets/`.
Keep `server/` free of `.h` and `.ino` files: CI's path filter would build a firmware release for
every server change.

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

### Status line (option C)

The service reads healthchecks.io's API (read-only key) and draws "server OK" or "N problems"
into the footer. It costs the device nothing. Where exactly it goes is layout work for the port.

## The container: CT 106

Modelled on CT 105; the build steps go into a runbook in the home-server repo.

- Debian 13, unprivileged, 1 core, **256 MB**, **3 GB** on `local-lvm`. Host RAM goes from about
  66% to about 68%.
- Static **192.168.1.212**, set in Proxmox with `--nameserver`, no Draytek binding (as the
  `.210` to `.249` rule says).
- No SSH server, unattended-upgrades, no Docker. It does not need to move to the apps VM later.
- `106.fw`:
  - **inbound:** TCP 8088 from `192.168.1.220` and from the `management` set only
  - **outbound:** TCP 443 to the internet (APIs, GitHub, healthchecks.io); to the LAN only HA on
    `192.168.1.18:80`
- Secrets in `/etc/inkplate-screen.env`, root-only: NS API key, HA webhook id, healthchecks.io
  ping URLs, healthchecks.io read-only key. Recorded by name in `reference.md`, values in Proton
  Pass.
- Service user `screen`, systemd unit sandboxed like the CT 102 jobs (`ProtectSystem=strict`,
  writes only to its own state folder).
- Monitoring: healthchecks.io check `screen-render` (pinged after every render, period 5 min,
  grace 15 min), `inkplate` (above), and possibly one for deploys. That brings the free plan to 8
  or 9 of 20. Optionally an Uptime Kuma HTTP monitor on `/preview.png`.
- Backups: the service holds no data that git does not have, apart from the env file (in Proton
  Pass) and the last telemetry file. Adding it to the `infra` vzdump job is cheap but optional.

## Deploying: the container pulls

The repo is public, so the container fetches over HTTPS with no key.

- A timer (`inkplate-screen-deploy.timer`, every 5 minutes) runs as its own user, `screen-deploy`,
  which owns `/opt/inkplate-screen/`. The service user can only read it.
- Each run: `git fetch` master. If the tree hash of `server/` is unchanged, stop. Otherwise:
  1. Export `server/` at that commit into `releases/<sha>/`.
  2. Create a venv and install with `pip install --require-hashes`.
  3. Run the self-test: render a frame from the fixtures and check it is 60,000 bytes. On
     failure, stop and keep the current release.
  4. Point the `current` symlink at the new release. A systemd path unit watching the symlink
     restarts the service, so the deploy user needs no root rights.
  5. Keep the last three releases.
- **Rollback:** `git revert` on master, which deploys like any change. By hand: point `current`
  at an older release.
- **Trust:** the server runs whatever reaches `master` of `lwittrock/inkplate-dashboard`. Only
  Lars can push there, and the self-test gates each deploy. Record this in the home-server repo's
  `decisions.md`.
- A commit that touches both firmware and `server/` deploys twice: the service within minutes, the
  device after midnight. That is why the contract must stay backward compatible.

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
2. **Service on the laptop.** HTTP, schedule policy, telemetry forwarding (to a dummy endpoint).
   *Done when:* `curl` gets 60,000 bytes and sensible headers for a range of simulated times.
3. **CT 106.** Build it from a new runbook in the home-server repo, with the deploy timer and both
   checks.
   *Done when:* a push to `server/` appears on the container by itself, `screen-render` is green
   for a week, and `/preview.png` opens from the laptop.
4. **Home Assistant.** Webhook automation, sensors, low-battery notification, `inkplate` check.
   *Done when:* a request from the laptop with made-up values shows up in HA, and a made-up 10%
   battery reaches the phone. Create the `inkplate` check, then pause it: healthchecks.io
   resumes a paused check on its next ping, which will be the device's first request in step 5.
5. **Thin firmware.** On a branch, bench-tested over USB against CT 106 with `checkForUpdates()`
   commented out for that flash (see `CLAUDE.md`). Test: normal wakes, stopping the service ("Server
   down" on the second failure), a wrong SSID ("No Wi-Fi"), recovery, the OTA safety net.
   Then update the `CONFIG_H` and `SECRETS_H` secrets, merge to master, and let the release build.
   *Done when:* the morning after, the wall shows the server's screen and HA shows telemetry.
6. **Clean up.** Rewrite `CLAUDE.md` for the thin client (the out-of-scope lines on remote logging
   and battery monitoring change), update `README.md`, mark the handoff as superseded, add a status
   note to `power-audit.md`. In the home-server repo: `reference.md` (CT 106, secrets, checks),
   the new runbook, `runbooks/home-assistant.md`, `decisions.md`, `plan.md`.
7. **After a week or two of data:** recompute the battery table, then decide on the fast Wi-Fi
   reconnect and on the cadence.

## Later, deliberately not now

- **Design improvements** beyond the port: after step 5, with the preview loop.
- **Grayscale:** the panel has a 3-bit mode, but it does not support partial refresh, so every
  wake would be a full, flashing refresh with more panel energy. Only with a reason.
- **Skipping unchanged frames:** the device sends a hash of its last frame, the service answers
  "unchanged", and the device skips download and refresh. That would save about 20% of a wake,
  but in the daytime the frame almost always changes (departures, the "updated" time).
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
