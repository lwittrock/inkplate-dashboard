# Inkplate 6 Weather & Train Dashboard

An e-paper dashboard for my commute: current weather, a 2-hour rain chart or 24-hour temperature
curve, the week ahead, and the next three trains from Den Haag to Tilburg Universiteit, in a
newspaper-style 1-bit layout on an Inkplate 6 behind glass on the wall.

Personal hobby project, shared in case it's useful as a reference. Not actively supporting forks;
if you want to adapt it to your city or route, expect to read the code.

> **Status, September 2026: moving to a server-rendered screen.** The server half (`server/`) is
> built and tested; the thin device firmware is on the `thin-client` branch until its switch-over
> ([docs/thin-client-switchover.md](docs/thin-client-switchover.md)). Until then the firmware on
> `master`, and on the wall, is the previous self-contained one, which fetched and drew everything
> itself.

---

## How it works

Two halves, one repo, because they share one contract:

- **The server** ([`server/`](server/), Python) runs on a small home server. Every 5 minutes it
  fetches Open-Meteo, Buienradar and NS, runs the train picker and the weather-station vote, and
  draws the whole 800×600 black-and-white frame. It also decides when the device should wake next.
- **The device** (the `*.ino` files, about 300 lines) wakes, joins Wi-Fi, makes one plain-HTTP
  request on the LAN, draws the frame it gets, and deep-sleeps for as long as the reply says. The
  request carries its battery level and firmware version, which the server passes on to Home
  Assistant and a health check.

Why: Wi-Fi time was ~92% of the battery budget, and five HTTPS calls per wake became one small LAN
request (modelled: about 40 days per charge becomes 3 to 5 months; to be measured). And a layout or
logic change no longer means a firmware update to a device behind glass. The reasoning, the
contract and the battery figures are in
[docs/server-rendering-design.md](docs/server-rendering-design.md).

---

## What's on the screen

- **Masthead**: an editorial greeting from today's weather and the day ("Bright Saturday", "Wet and
  windy Friday"), with specials for new year, Koningsdag, the solstices, Christmas and so on; the
  date; and a sun or moon arc showing where we are between sunrise and sunset.
- **Current weather**: a 128 px icon, the temperature, wind speed and direction. Current
  conditions come from a vote among nearby KNMI stations, so one faulty sensor can't set the icon.
- **Right of the weather**: a 2-hour rain chart when rain is coming, else a 24-hour temperature
  curve with sunrise and sunset marked.
- **Week strip**: seven days, each with an icon and a min/max range bar.
- **Trains**: three cards from Den Haag Centraal to Tilburg Universiteit, with platform, delay,
  the transfer at Breda and the arrival time. When a Centraal train is cancelled or badly late, a
  clean alternative from Den Haag HS takes its card, marked with a black "DH HS" pill.
- **Footer**: when it was drawn, and the battery.

`python -m screen.preview` in `server/` renders the current screen on a laptop.

---

## Where things are written down

Each fact has one home; the others point to it.

| Question | Document |
|---|---|
| What is this, how is it built, where to start | this README |
| Why this design, and **the contract** between device and server | [docs/server-rendering-design.md](docs/server-rendering-design.md) |
| The server code: layout, running it, testing, how it deploys | [server/README.md](server/README.md) |
| The firmware: rules, OTA, CI, and the gotchas that cost time | [CLAUDE.md](CLAUDE.md) |
| The one-off bench test and release of the thin client | [docs/thin-client-switchover.md](docs/thin-client-switchover.md) |
| Battery figures | [docs/power-audit.md](docs/power-audit.md) |
| The home server side: the container, its firewall, the Home Assistant sensors, the checks | the private home-server docs repo, `runbooks/inkplate-screen.md` |
| How the choice was made (historical) | [docs/homeserver-integration-handoff.md](docs/homeserver-integration-handoff.md) |

---

## Hardware

- **[Inkplate 6](https://inkplate.io/)**: ESP32, 800×600 1-bit e-ink display (Soldered Electronics),
  with a 3.7 V LiPo on its onboard charger.
- **A small always-on machine on the LAN** for the server half; here a Proxmox container with
  256 MB of RAM.

---

## Updates

- **Server:** a push to `master` that touches `server/` is live on the home server within about 5
  minutes, if its self-test passes; a failing one never goes live. A GitHub workflow runs the full
  server tests on the same push.
- **Firmware:** a push to `master` that touches `*.ino`, `*.h` or `Fonts/` builds a release
  (GitHub Actions, [.github/workflows/release.yml](.github/workflows/release.yml)), which the
  device installs by itself shortly after midnight. App-level rollback reverts a release that
  keeps failing to reach Wi-Fi. `[skip release]` in the commit message skips one. CI builds from
  the `CONFIG_H` and `SECRETS_H` Actions secrets, not from local files.
- **Flashing by USB** is needed once (partition scheme "Minimal SPIFFS", board "Soldered
  Inkplate6") and for bench tests. See [CLAUDE.md](CLAUDE.md), "Build & Flash", before doing
  either: a local build replaces itself with the latest release unless told not to.

---

## Attribution

- **[Inter](https://rsms.me/inter/)** by Rasmus Andersson, used under the SIL Open Font License 1.1
  ([Fonts/OFL.txt](Fonts/OFL.txt)). Converted to GFX fonts via
  [rop.nl/truetype2gfx](https://rop.nl/truetype2gfx/).
- **[Buienradar](https://www.buienradar.nl/)**: KNMI station observations and the 2-hour rain
  nowcast. Free; attribution required for commercial use, which this isn't.
- **[NS API](https://apiportal.ns.nl/)**: trains, via the Reisinformatie v3 Trip Planner.
- **[Open-Meteo](https://open-meteo.com/)**: the hourly curve and the 7-day forecast.
- **[Inkplate](https://inkplate.io/)** hardware and library by Soldered Electronics.

---

## Security caveats

- The device's request is plain HTTP on the home LAN: the picture and the battery report are not
  secret, and TLS handshakes were what drained the battery. Any device on the LAN could send a fake
  battery report.
- The firmware update check uses `setInsecure()`: no certificate validation.
- The NS API key lives on the server, not on the device.

---

## License

Code: MIT, see [LICENSE](LICENSE). Inter font files: SIL Open Font License 1.1, see
[Fonts/OFL.txt](Fonts/OFL.txt) and [server/screen/assets/ttf/OFL.txt](server/screen/assets/ttf/OFL.txt).
Material Symbols (the weather icons): Apache 2.0, see
[server/screen/assets/ttf/LICENSE-MaterialSymbols.txt](server/screen/assets/ttf/LICENSE-MaterialSymbols.txt).
