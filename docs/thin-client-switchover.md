# The thin client: bench test and switch-over

Written 23 September 2026 for step 5 of [server-rendering-design.md](server-rendering-design.md)
(Phase 10.4 in the home-server repo's `plan.md`). The firmware is on the `thin-client` branch; it
compiles (1,113,605 bytes, 56% of an OTA slot) but has never run on the device. This is the
checklist for the evening it does. Each step ends with a **Done when**.

## Before you start

- **CT 106 serves the screen:** the home-server runbook `runbooks/inkplate-screen.md` steps 0
  to 17 are done, and `http://192.168.1.212:8088/preview.png` shows today's dashboard.
- **The device is off the wall** and on USB. Arduino IDE: board "Soldered Inkplate6",
  partition scheme `min_spiffs`, serial monitor at 115200.
- **On the laptop:** `git checkout thin-client`, and in the local `config.h` (gitignored, so this
  touches nothing else):

  ```c
  #define DEBUG_LOG 1
  #define OTA_SKIP 1            // else the "dev" build replaces itself with the newest release
  #define BENCH_MAX_SLEEP_S 60  // a wake a minute instead of every 15 to 30
  ```

  The static-IP lines stay as they are. Nothing else in `config.h` matters any more.

## 1. The first wake

Upload, open the serial monitor.

**Done when** the log shows `Wi-Fi: 192.168.1.220`, `Screen: HTTP 200`, `Screen: frame 60000
bytes` and `Full refresh`, and the panel shows the same dashboard as `preview.png`. In HA,
`sensor.inkplate_firmware` reads `dev` and `sensor.inkplate_last_seen` is now.

## 2. The second wake

Wait a minute.

**Done when** the log shows `wake #1`, `Screen: HTTP 200` and `Partial refresh` (the server asks
for full only every fourth wake), and the panel still looks right. HA's `Inkplate awake time`
and `Wi-Fi connect time` now have values: the previous wake's.

## 3. Server down, and back

Host shell: `pct exec 106 -- systemctl stop inkplate-screen`. Watch two wakes.

**Done when:**
- the first failed wake logs `Wake failed: server, streak 1` and the panel is unchanged;
- the second logs `streak 2`, `OTA: skipped, OTA_SKIP is set` (the failure trigger fired; the
  real check is skipped on the bench) and the panel reads **Server down**;
- the third leaves the panel alone.

Then `pct exec 106 -- systemctl start inkplate-screen`. **Done when** the next wake logs
`Screen: HTTP 200` and `Full refresh` and the dashboard is back.

## 4. No Wi-Fi

In the local `secrets.h`, change one letter of `WIFI_SSID`, upload, and watch two wakes. Then put
the letter back and upload again.

**Done when** the second wake shows **No Wi-Fi** on the panel, and after the corrected upload the
dashboard returns. No rollback happens: a USB flash has no pending OTA version, so the
boot-attempts counter has nothing to roll back to.

## 5. Optional: a night wake

Only if the bench session runs past 23:30, or with the service's clock moved: the server answers
`204`. **Done when** the log shows `Screen: HTTP 204` and the panel is unchanged.

## Release

1. **The local `config.h`:** remove `OTA_SKIP` and `BENCH_MAX_SLEEP_S`, and set `DEBUG_LOG` back
   to how production has it (off).
2. **The `CONFIG_H` secret** needs no change: its old fields are ignored, and there is no new
   required one. Check it does not contain `OTA_SKIP` or `BENCH_MAX_SLEEP_S`; CI would refuse to
   build.
3. **Merge `thin-client` into `master` and push.** CI builds and publishes the release.
   **Done when** the Actions run is green and `firmware-latest`'s `version.txt` names the new tag.
4. **Converge the device on the release.** USB-flash the local build once more, now without
   `OTA_SKIP`. On its first wake it is "dev", checks the manifest (cold boot), finds the new tag,
   updates and reboots. **Done when** HA's `sensor.inkplate_firmware` shows the new tag.
5. **Back on the wall.** The home-server runbook's step 18: the `inkplate` check.

**Done when (the whole switch-over):** the morning after, the wall shows the server's screen,
HA shows the night's reports, and `inkplate`, `screen-render` and `screen-deploy` are green.

## A week later: the OTA path itself

The bench skipped the real OTA check. It is proven by the first release after this one: push any
small firmware change (a comment is enough) and see the device take it via the server's `X-Ota`
hint around 00:05. **Done when** HA's firmware sensor shows the newer tag the next morning.

## If something goes wrong on the wall

- **The panel says "Server down"**: CT 106 or its service is down (`pct exec 106 -- systemctl
  status inkplate-screen`). The device retries hourly and recovers by itself.
- **The panel says "No Wi-Fi"**: the router or the Deco.
- **The panel shows an old dashboard and HA's `last seen` stopped**: the device is dead, flat, or
  stuck; `inkplate` alerts by 08:00 at the latest. USB is the way in.
- **A bad release:** after three wakes without Wi-Fi the device rolls back to the previous slot by
  itself. A release that reaches Wi-Fi but breaks the server request is replaced by the next
  release: the device checks for updates on its second failed wake and every 12 hours after.
