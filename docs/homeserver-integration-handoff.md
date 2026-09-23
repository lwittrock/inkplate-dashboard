# Handoff: the Inkplate dashboard and the home server

Written 23 September 2026 at the end of a long home-server session, for the next session that
picks this up, whether that is Lars or an AI agent. Nothing described here as an option has been
built. The only change already made is the address record under "Already settled".

## The goal in one paragraph

The Inkplate 6 on the wall is a self-contained device: it wakes every 15 to 30 minutes, fetches
weather (Buienradar, Open-Meteo) and trains (NS Trip Planner) straight from the internet, draws the
screen, and sleeps. It has no connection to the home server at all. Lars wants it better
integrated. Four options came out of the discussion, from small to large, and Lars liked all four
without choosing an order. The first job of the next session is to agree which one to do first.

## Read first

**This repo** (`lwittrock/inkplate-dashboard`, checked out at
`C:\AAA\python-projects\inkplate_test\Dashboard`):

- [`CLAUDE.md`](../CLAUDE.md), all of it. Above all "OTA gotchas", "boot-path discipline", the
  `config.h` rename workflow, and "OTA out of scope".
- [`power-audit.md`](power-audit.md): Wi-Fi-active time is about 92% of the battery budget. Every
  option below is judged partly by what it adds to that.

**The home-server repo** (`lwittrock/homeserver-docs`, at
`C:\AAA\python-projects\homeserver-docs`):

- `CLAUDE.md`: what an agent may do alone, must ask about, and never does. It has teeth: a
  `.claude/settings.json` there enforces part of it.
- `README.md`, "Running a session on the server": one step at a time, name the surface before
  every command, never ask for a secret in the chat, stop at every "Done when".
- `reference.md`: what exists, what watches what, and the secrets inventory by name.
- `runbooks/home-assistant.md`, `runbooks/adding-a-job.md`, `runbooks/networking.md`.

## Facts that constrain every option

**Firmware side**

- **A push to `master` that touches `*.ino`, `*.h` or `Fonts/**` builds a release that the device
  installs by itself after midnight.** The device is behind glass with no reachable button. A
  change that stops `setup()` before `updateDisplay()` returns rolls back at best and needs a
  physical reflash at worst. Network code and anything in the boot path need a USB test first; the
  order for that is in `CLAUDE.md` ("push, wait for green, THEN USB flash"), because a local build
  otherwise replaces itself with the newest release.
- **CI builds from the `CONFIG_H` and `SECRETS_H` Actions secrets, not from local files.** A new
  field means updating the secret before pushing. `secrets.h` is read-denied for Claude in this
  repo; keep it that way.
- **The battery voltage is already read**: `display.readBattery()` in `C_Display.ino` feeds the
  footer icon. Nothing sends it anywhere.
- **Night mode, 23:30 to 06:30**: the device skips fetching and sleeps. Any "is it alive" check
  must allow a seven-hour gap every night. Outside peak windows it wakes every 30 minutes.
- **Deliberate past decisions touched here.** `CLAUDE.md` lists "remote logging back to a server"
  as out of scope and "no battery monitoring code exists". Options A and B are not logging in that
  sense, but they do reverse the second point. Record the change of mind in `CLAUDE.md` when one
  ships, rather than letting the old line contradict the code.

**Home-server side**

- **Address `192.168.1.220`** belongs to the Inkplate: static in its firmware, bound to
  `10:97:BD:DA:4A:F4` on the Draytek. See "Already settled".
- **Home Assistant** is VM 100 at `http://192.168.1.18` (port 80, not 8123). A webhook trigger
  with `local_only: true` accepts calls from the LAN only, which suits a device at home. A webhook
  id is the only authentication, so it is treated as a secret.
- **healthchecks.io** holds 6 checks of the free plan's 20. Checks alert by ntfy and email and do
  not depend on the server, which is what makes them right for "the server itself is down" and
  equally for "this device went quiet".
- **Jobs run in CT 102**, one per the six-piece pattern in `runbooks/adding-a-job.md`: own user,
  own env file, sandboxed unit, a timer, its own healthchecks.io check. **CT 102 listens on
  nothing and drops all inbound traffic by design**, so it cannot serve a file to the Inkplate.
- **New Docker services** go into the `apps` VM once it exists (Phase 8, waiting on drive prices).
  Small private additions can still go into LXC; the host's RAM (12GB, about 66% used) and the
  overbooked thin pool are the real limits.
- **Private first.** Lars chose on 23 September to keep the Homepage dashboard off the internet.
  Nothing here needs to be public either.

## The four options

### A. A healthchecks.io check: know when the dashboard dies

The device pings a check, say `inkplate`, so a flat battery or a crash loop reaches the phone
instead of showing as a stale screen nobody notices.

- **Firmware:** one HTTPS GET to the ping URL, not on every wake: the first successful wake
  after 06:30 and then hourly is enough. Put it after `updateDisplay()`, so a ping means "a screen
  was drawn", and make a failed ping change nothing else.
- **Check:** healthchecks.io's cron mode fits the night gap better than a simple period, for
  example a schedule for daytime hours with a grace of an hour or two. Work the schedule out
  against `nextSleepSeconds()` and night mode, and test that a normal night does not alert.
- **Secret:** the ping URL goes in `secrets.h` and `SECRETS_H`, and in the home-server repo's
  `reference.md` by name only.
- **Cost:** a TLS handshake roughly once an hour, well under 1% of the budget. Estimate it against
  `power-audit.md`'s figures before shipping.

### B. Battery and status into Home Assistant

The device posts `{battery_v, firmware, wake_counter, rssi}` to an HA webhook. HA turns it into
sensors (a trigger-based template sensor on the webhook trigger is the tidy way), shows them, and
notifies the phone below a threshold, for example 15%.

- **Firmware:** one plain-HTTP POST to `http://192.168.1.18/api/webhook/<id>` on the LAN. It needs
  no TLS, so it is cheaper than A. The webhook id goes in `secrets.h` and `SECRETS_H`.
- **HA:** the webhook automation, the sensors, and a low-battery notification to
  `mobile_app_lars_phone`, in the style of the existing Proxmox alert automation.
- **Home-server records:** the webhook id in `reference.md`'s secrets inventory, the automation in
  `runbooks/home-assistant.md`, the sensor on Homepage if wanted.
- **A and B together:** B can also notice silence (a "last seen" older than eight hours), but
  that alert runs through HA on the server. A does not. Doing both is cheap.

### C. A server status line on the display

A short line, for example in the footer: "server OK" or "1 problem", so the wall shows the home
server's state at a glance.

- **Source:** the healthchecks.io API with a read-only key gives up/down per check. Uptime Kuma's
  status page `home` (`http://192.168.1.18:3001/api/status-page/...`) gives the monitors without a
  key. One small request either way, but it is another fetch per wake: consider every hour only.
- **Layout:** the screen is laid out to the pixel (`C_Display.ino`, the band comments are the
  contract). Finding room is the real work.
- **Key:** a separate read-only healthchecks.io key for the Inkplate rather than reusing
  Homepage's, so either can be revoked alone. Recorded in `reference.md`.

### D. Let the server draw the screen

The biggest change and the biggest payoff. A job on the server fetches the weather and trains,
renders the 800x600 1-bit image, and the Inkplate only downloads that picture each wake.

- **Why:** five HTTPS calls per wake, two of them about 90KB of train JSON, become one small
  image, possibly over plain HTTP on the LAN. Wi-Fi time is the battery budget, so the
  runtime could improve a lot (to be measured, not modelled). Layout and logic changes would
  happen on the server, **without risking an update to the device behind glass**: the firmware
  becomes a thin client that rarely changes. It also gives "Buienradar and NS in HA", on the
  home server's ideas list, the concrete use it was waiting for.
- **The port:** the logic in `A_Calculations.ino` is not trivial: the per-slot CTR/HS train picker
  and the Buienradar consensus vote are both documented in `CLAUDE.md` and would move to, say,
  Python with Pillow. The masthead's editorial headlines and the Bayer-dithered rain chart move
  with them.
- **Open design questions, to settle before any code:**
  1. **Where it renders:** a CT 102 job is the obvious home, but see the next point.
  2. **Where the device fetches the image from:** CT 102 serves nothing by design. Candidates: a
     small static server in CT 105 next to Homepage (one more port in `105.fw`'s inbound rule),
     or Home Assistant's `/local/` folder, which HA serves without a login to the LAN. Each has
     a firewall and trust consequence to write down.
  3. **Image format** the Inkplate library draws from a URL (PNG, BMP, raw bitmap), and whether
     the 1-bit output should be produced server-side exactly as the panel shows it.
  4. **When the server is down:** the device must keep the last good picture and mark it stale
     ("as of 14:30"), never go blank. That is the price of the dependency.
  5. **Staging:** keep the current firmware path working until the server path has run for a
     week, perhaps as a `config.h` switch, so a rollback is a config change, not a reflash.
- **Size:** a project of several sessions. Worth a design document of its own in `docs/` first,
  the way `power-audit.md` came before the power changes.

## Suggested order

B first: it uses the battery reading that already exists, needs no TLS, and produces the most
practical thing (a low-battery warning). Then A, cheap and independent of the server. C when
there is appetite for layout work. D as its own project, starting with the design questions
above. Lars has not confirmed this order.

## Already settled (23 September 2026)

`192.168.1.220` was missing from the home-server docs, whose rule was that new containers take
any address from `.210` to `.249` without a router binding: the next container could have been
given the Inkplate's address. It is now recorded as taken in `reference.md` (Network),
`runbooks/networking.md` and `plan.md` (Current state and the standing habits) of the home-server
repo. Nothing on any machine was changed.

## How Lars works, from this session

- Commands on the server are his to run, one at a time, each with its surface named and a "Done
  when". An agent may read the host through the read-only rclone `host:` remote on its own; it
  asks before anything else touches a machine.
- Search a known path, never the whole laptop: a file search reached Phone Link's view of the
  phone on 23 September.
- His documents use no em dashes, and each change to the home-server repo is committed with the
  reason, after `pwsh ./check-docs.ps1` passes.
- He weighs value against effort openly and says no to things; "not now" is recorded as such, not
  dropped.
