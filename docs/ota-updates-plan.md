# OTA Updates Plan

Goal: deploy new firmware to the Inkplate without removing it from the picture frame.
Mechanism: ESP32 OTA pulling a `.bin` from a GitHub release, gated by a one-line manifest the device checks once per day inline with the morning data fetch.

Status: **planned, not implemented.** This doc captures decisions, hardware constraints, and the phased build order so we don't lose context between sessions.

---

## Confirmed Inkplate 6 hardware specs

Verified against the Inkplate hardware reference and Soldered docs (May 2026):

| Spec | Value | Source |
|---|---|---|
| ESP32 module | ESP32-WROVER (PCB antenna, BLE) | Inkplate hardware reference |
| Flash | **4 MB** | Inkplate hardware reference |
| PSRAM | 8 MB | Inkplate hardware reference |
| CPU | dual-core @ 240 MHz | Inkplate hardware reference |
| Battery connector | JST (1200 mAh Li-Po stock) | Inkplate docs |
| Deep sleep current | ~30–40 µA (per CLAUDE.md bench note) | Project measurement |
| Active current (WiFi + render) | ~80–200 mA depending on phase | Various |
| USB-serial | CH340 (no USB-host capability) | Schematic |

**Why this matters for OTA:**

- 4 MB flash is the *minimum* for OTA. With a 2-slot OTA partition table we get **two ~1.25 MB app slots + ~1.4 MB SPIFFS** (default scheme) or **two ~1.9 MB app slots + 190 KB SPIFFS** (Minimal SPIFFS scheme). The current binary is well under 1.25 MB so either works.
- The CH340 means the only non-OTA path is physical USB-C access. Hence this project.
- 8 MB PSRAM is irrelevant to OTA (OTA writes to flash, not RAM) but worth recording.

---

## Battery analysis

Baseline today (best-effort estimate, no measurement on the bench yet):

- Night mode 23:00–07:00 means actual wakes ≈ **64/day** (16 h × 4/h), not 96.
- Deep sleep: 0.035 mA × 24 h = **~0.8 mAh/day**
- 64 wakes × ~0.28 mAh/wake = **~18 mAh/day**
- Total: **~19 mAh/day** → 1200 mAh stock ≈ **~60 days runtime** (rough)

OTA check cost — but **the manifest fetch is bundled into the morning's existing WiFi+TLS session** (right after `fetchOpenMeteo` succeeds), so the marginal cost is one extra GET on an already-warm connection, not a fresh radio-on cycle. Estimated **<0.01 mAh/day**. Negligible vs. baseline.

Actual OTA download (~1 MB binary, only when an update is available):

- ~5–10 s @ ~150 mA WiFi + ~5–10 s @ ~50 mA flash write = **~0.5 mAh per update**
- Negligible at any realistic tagging cadence.

**Decision: check once per day, inline with the morning data fetch.** Cadence is gated by `RTC_DATA_ATTR lastCheckDay` (current day-of-year compared to stored value). Failed checks back off for 24h — no aggressive retry burning radio time.

---

## Architecture

```
┌────────────────────┐         ┌──────────────────────┐
│ GitHub Actions     │  tag    │ GitHub Release       │
│ arduino-cli build  ├────────►│ firmware.bin         │
│ on tag push        │         │                      │
└────────────────────┘         └──────────────────────┘
        │ pages publish
        ▼
┌──────────────────────┐
│ version.txt          │  ◄── lwittrock.github.io/inkplate-dashboard/version.txt
│ line 1: version      │      (or raw.githubusercontent.com on a stable branch)
│ line 2: bin URL      │
└──────────┬───────────┘
           │ HTTPS (once/day, warm TLS)
           ▼
┌──────────────────────┐
│ Inkplate (morning)   │
│ 1. fetch version.txt │
│ 2. compare version   │
│ 3. httpUpdate.update │
│ 4. reboot            │
│ 5. mark-valid on     │
│    successful render │
└──────────────────────┘
```

### Manifest format — plain text, not JSON

Two-line text file. No parser, no JSON heap pressure, no schema versioning:

```
2026.05.25-1
https://github.com/lwittrock/inkplate-dashboard/releases/download/v2026.05.25-1/firmware.bin
```

- Version format: `YYYY.MM.DD-NN` with **zero-padded** suffix (`-01`, `-02`, ..., `-99`). Zero-padding is required: lexical compare of `-9` vs `-10` puts `-9` second, which would suppress legitimate updates. 99 tags/day is a ceiling we'll never reach.
- Embedded in firmware via `#define FIRMWARE_VERSION` (CI injects this at compile time as `-DFIRMWARE_VERSION="..."`).
- Hosted on a dedicated **`firmware-latest`** branch in the repo, fetched via `https://raw.githubusercontent.com/lwittrock/inkplate-dashboard/firmware-latest/version.txt`. No 302 redirect, no GitHub Pages setup needed — CI just commits `version.txt` to that branch on every tag. The binary itself still lives in GitHub releases (for the UI + history); only the manifest needs flat hosting.
- The binary URL on line 2 *does* 302 from `github.com/.../releases/download/...` to `objects.githubusercontent.com` — `HTTPUpdate` needs `httpUpdate.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS)` to handle that.

### On-device update logic (new file: `D_OTA.ino`)

State (all `RTC_DATA_ATTR`, ~50 bytes total):
```c
RTC_DATA_ATTR uint16_t lastCheckDay        = 0;     // day-of-year of last check
RTC_DATA_ATTR uint8_t  bootAttempts        = 0;     // ++ at top of setup(), reset on render
RTC_DATA_ATTR char     pendingVersion[24]  = {0};   // set before httpUpdate.update()
RTC_DATA_ATTR char     lastFailedVersion[24] = {0}; // set on rollback; skipped on future checks
```

Functions:
- `bool shouldCheckForUpdate()` — true if local time hour ≥ 7 AND `dayOfYear() != lastCheckDay`.
- `bool fetchManifest(char* outVersion, char* outUrl)` — slurp version.txt, split on newline. Both lines bounded to 23/255 chars.
- `void performUpdate(const char* version, const char* url)` — copy `version → pendingVersion`, call `httpUpdate.update(client, url)`. On success: reboot. On failure: log, clear pendingVersion, copy version → lastFailedVersion, continue.
- `void markFirmwareValid()` — called at the very end of `setup()`, after `updateDisplay()` returns. Resets `bootAttempts = 0` and clears `pendingVersion`. (Does NOT need `esp_ota_mark_app_valid_cancel_rollback()` — see rollback section.)
- `void checkBootAttempts()` — first thing in `setup()`. If `bootAttempts >= 3` AND `pendingVersion[0]` is set: copy → `lastFailedVersion`, call `esp_ota_set_boot_partition()` on the other slot, `esp_restart()`. Otherwise `bootAttempts++`.

**No manual-skip button.** Frame is fully enclosed; if the device is genuinely stuck and rollback didn't help, the only recovery is physical removal + USB reflash — at which point a full reboot is the natural reset path anyway.

### Rollback strategy — app-level, not bootloader-level

**Why not bootloader rollback?** The stock Arduino-ESP32 bootloader does **not** set `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`. `esp_ota_mark_app_valid_cancel_rollback()` is a no-op in this build environment. Calling it gives a false sense of safety.

**App-level rollback (what we actually implement):**
1. `RTC_DATA_ATTR uint8_t bootAttempts` increments at the very top of `setup()`.
2. If it reaches 3 with `pendingVersion` still set (meaning we shipped an OTA and haven't completed a render cycle in 3 boots), we call `esp_ota_set_boot_partition(prev_slot)` and reboot.
3. End of `setup()` (after `updateDisplay()` returns successfully) resets `bootAttempts = 0` and clears `pendingVersion`.

Failure modes this covers:
- ✅ Crash early in `setup()` → reboots, counter hits 3, reverts.
- ✅ Boot loop from any cause → same.
- ❌ Boots fine, renders fine, but does the *wrong thing* (e.g. wrong location, broken layout) → no rollback. This is the "silent semantic drift" case. Only mitigation is discipline: don't change config field semantics without renaming.

### GitHub Actions pipeline (new file: `.github/workflows/release.yml`)

- Trigger: `on: push: tags: ['v[0-9]*']` — only digit-starting tags trigger CI, so `v-experiment`, `vTEST`, etc. can be pushed without spawning a release.
- Permissions: workflow needs `permissions: contents: write` to create releases and push to `firmware-latest`. Default `GITHUB_TOKEN` is read-only on newer repos.
- Commit identity: `git config user.name "Lars Wittrock"` + `user.email "<id>+lwittrock@users.noreply.github.com"` (GitHub noreply form — keeps real email out of public commit history).
- Steps:
  1. Install arduino-cli + Inkplate board package via e-Radionica board manager URL.
  2. Install ArduinoJson v7.
  3. Inject `secrets.h` from GitHub Actions secrets (`WIFI_SSID`, `WIFI_PASSWORD`, `NS_API_KEY`).
  4. `config.h` is the **CI-injected source of truth** — stored as a single Actions secret containing the whole file contents, written to disk before compile. Critical: CI must own `config.h`, not local edits. (See CLAUDE.md note on boot-path discipline.)
  5. Compile with `--build-property "build.extra_flags=-DFIRMWARE_VERSION=\"${TAG#v}\""`. The escaping is the most common arduino-cli footgun — test the workflow with a dry-run tag first.
  6. Publish `.bin` as a release asset.
  7. Update `version.txt` on the `gh-pages` (or `firmware-latest`) branch with the new tag + binary URL.

---

## Implementation phases

**Reordered from earlier draft** — CI comes before real OTA, so the first binary that ever goes over the air is one that was already validated via USB.

### Phase 0 — Pre-flight checks (already done, May 2026)

Confirmed:

- **Current partition scheme:** "Huge APP (3MB No OTA / 1MB SPIFFS)" — **not OTA-capable** (single app slot).
- **Target partition scheme:** **"Minimal SPIFFS (1.9MB APP with OTA / 190KB SPIFFS)"** — two app slots + 190 KB SPIFFS.
- **Inkplate library SPIFFS usage:** none. Grep of `c:\Users\larwi\OneDrive\Documents\Arduino\libraries\InkplateLibrary` for `SPIFFS.begin` and `LittleFS.begin` returned zero matches. 190 KB SPIFFS is safe — nothing uses it.

**One-time prep step** (do this right before Phase 2, not now): in Arduino IDE → Tools → Partition Scheme → switch to "Minimal SPIFFS (1.9MB APP with OTA/190KB SPIFFS)" → flash over USB once. After this single reflash, OTA is unlocked permanently.

### Phase 1 — CI build pipeline (no device changes) — ✅ COMPLETED 2026-05-25

**Goal:** prove the GitHub Actions workflow produces a working binary.

**What was actually done:**
- Tagged `v2026.05.25-00` as the pre-OTA baseline (commit `660eefd`).
- Added `.github/workflows/release.yml` with pinned versions:
  - `Inkplate_Boards:esp32` core **8.1.0**
  - `InkplateLibrary` **11.1.0**
  - `ArduinoJson` **7.4.3**
- Added `CONFIG_H` and `SECRETS_H` to GitHub Actions secrets (whole-file form).
- Created the orphan `firmware-latest` branch with a placeholder `version.txt`.
- Added `FIRMWARE_VERSION` injection via CI-generated `firmware_version.h` (gitignored); Dashboard.ino has a `"dev"` fallback for local builds.
- CI publishes `Dashboard.ino.bin` as a release asset AND updates `version.txt` on `firmware-latest`.
- Tag `v2026.05.25-03` produced binary size **1.18 MB** vs. local **1.17 MB** — within rounding, build environments match. Functional USB-flash validation deferred (rollback in Phase 3 is the safety net).

**Hiccups worth remembering** (now captured in CLAUDE.md "CI / arduino-cli build gotchas"):
- v01 failed because arduino-cli requires the sketch folder name to match the main `.ino` filename. Repo checks out as `inkplate-dashboard/` but main file is `Dashboard.ino`. Fix: workflow stages files into a `sketch/Dashboard/` folder before compile.
- v02 failed because the Soldered "Inkplate_Boards" package has two boards both colloquially called "Inkplate6". The IDE picker's "Soldered Inkplate6" is FQBN `Inkplate6V2` (defines `ARDUINO_INKPLATE6V2`). The legacy `Inkplate6` FQBN has `build.board=ESP32_DEV` which fails to define any board macro the v11+ Inkplate library accepts → `#error "Board not selected!"`. Fix: workflow FQBN is `Inkplate6V2`, not `Inkplate6`.

**Risk:** zero — device behavior is unchanged. You're only validating the build pipeline.

### Phase 2 — Plumbing (manifest check, no real update)

**Goal:** verify the on-device version-check pipeline end-to-end without risking a brick.

1. Add `#define FIRMWARE_VERSION` injected by CI.
2. Add `D_OTA.ino` with `shouldCheckForUpdate()`, `fetchManifest()`, and the RTC state — **but `performUpdate()` only logs "would update from X to Y", doesn't actually update.**
3. Add the inline call in `setup()` right after `fetchOpenMeteo` succeeds.
4. Hand-publish a `version.txt` with a fake newer version.
5. Watch serial logs over a full day to confirm:
   - Manifest fetches succeed
   - Daily cadence triggers exactly once (around the morning's first wake after 07:00)
   - Version comparison works as expected
   - `otaSkipWakesLeft` works when WAKE is held

**Risk:** zero — `performUpdate` is a no-op.

### Phase 3 — Real OTA with app-level rollback — ✅ IMPLEMENTED 2026-05-25 (deployment + testing pending)

**Goal:** actual self-update working, with the rollback safety net.

**What landed:**
- `performUpdate(version, url)` — calls `httpUpdate.update()` with `HTTPC_FORCE_FOLLOW_REDIRECTS` (GitHub release URL 302s to `objects.githubusercontent.com`). Stages `otaPendingVersion` before the download so rollback detection can identify in-flight OTAs.
- `checkBootAttempts()` — runs at the top of `setup()` (after `initOtaState()`, before `initHardware()`). Increments counter; if it hits `OTA_BOOT_FAILURE_LIMIT` (3) AND `pendingVersion` is set, calls `esp_ota_set_boot_partition()` on the other slot and `esp_restart()`. App-level rollback, not bootloader-level.
- `markFirmwareValid()` — runs **right after `initHardware()`**, NOT at end of `setup()`. The original "end of setup" plan would have broken catastrophically because `handleNightMode()` calls `goToSleep()` without returning during 23:00–07:00. Every night-time wake would have incremented `bootAttempts` without resetting it → false rollback within ~45 minutes. New rule: if WiFi+NTP came up, firmware is healthy enough to mark valid.
- "Dev build" special case: a local firmware compiled without CI's `firmware_version.h` shows `FIRMWARE_VERSION="dev"`, which lexically sorts > any digit-starting version. Without an exception, dev builds could never OTA-pull a tagged release. Fix: if local version is literally `"dev"`, any tagged remote is treated as newer.
- Footer version display gated by `SHOW_VERSION_FOOTER` in `config.h` — appears as `Updated HH:MM  ·  2026.MM.DD-NN` next to the timestamp. Flip the define off once trusted.

**Failure modes covered:**
- ✅ New firmware crashes early in `setup()` → bootAttempts climbs, rollback fires after 3 boots.
- ✅ New firmware boot loops → same as above.
- ✅ Cold boot (battery removal) doesn't trigger spurious rollback — RTC magic sentinel resets state cleanly.
- ❌ New firmware boots, `initHardware()` returns OK, but later code renders wrong content — markFirmwareValid already fired. No automatic mitigation. Defense: tag discipline + local USB test before tagging.

**Deployment + testing checklist (pending):**
1. User pulls Phase 3 code, re-uploads via Arduino IDE on `min_spiffs` partition (still `FW: dev` locally).
2. On next wake, device fetches manifest, sees v05 tagged release, calls `performUpdate` (dev → tagged is always treated as upgrade).
3. Device reboots into the new slot, runs v05 firmware, hits `markFirmwareValid()` after initHardware — logs `marked valid after update to 2026.05.25-05`.
4. Footer should now read `Updated HH:MM  ·  2026.05.25-05`.
5. **Test rollback** (optional but recommended): tag a deliberately-broken release whose `setup()` calls `esp_restart()` right after `checkBootAttempts()`. Device pulls it, boots 3 times in rapid succession, rolls back to v05.

**Risk on deployment:** medium. Mitigated by: app-level rollback for crashes; `lastFailedVersion` to skip known-bad versions; tag discipline.

---

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Check cadence | once per day, inline with morning Open-Meteo fetch | Reuses warm WiFi/TLS session; near-zero battery cost |
| Update trigger | git tag (not every commit) | Manual "I've verified this works" gesture; safer for a hard-to-recover device |
| Manifest format | 2-line text file on GitHub Pages | Simplest possible; no JSON parse, no schema versioning |
| Binary hosting | GitHub release asset | Free UI + version history; redirect handled by `setFollowRedirects` |
| Verification | trust HTTPS, no signing or SHA256 | Personal device, only source is own repo; signed updates add disproportionate complexity. ESP32 OTA validates binary header magic regardless |
| Partition scheme | Whatever Phase 0 reveals — default is fine if 2-slot, else Minimal SPIFFS | Current binary fits comfortably in either |
| Rollback strategy | **App-level RTC counter**, NOT bootloader rollback | Stock Arduino-ESP32 bootloader lacks `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`; app-level works without rebuilding the bootloader |
| Manual recovery | None — frame is fully enclosed, physical pull + USB reflash is the only path if rollback also fails | Touchpad-based skip considered but rejected (no accessible button inside the frame) |
| Failed-version handling | Record `lastFailedVersion`, never retry | A failed binary is probably actually bad; force a new tag to recover |
| RTC RAM on cold boot | `RTC_DATA_ATTR uint32_t rtcMagic` sentinel — if magic doesn't match on boot, zero all OTA state | Cold boot (battery removal) leaves RTC slow memory undefined, could trigger spurious rollback otherwise |
| Footer version display | Tiny version string in the footer next to "Updated HH:MM", toggled by `SHOW_VERSION_FOOTER` define in `config.h` | Useful during initial OTA shakedown; can be turned off by flipping the define once trusted |

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bad build crashes before render | App-level rollback after 3 failed boots (`checkBootAttempts`) |
| Bad build boots fine but renders wrong content ("silent drift") | No automatic mitigation. Defense: discipline + local USB test before tagging + footer version line so misbehavior is at least visually obvious |
| Manifest fetch hangs and burns battery | 8 s HTTPS timeout (already used elsewhere); update check is opportunistic, never blocks the render |
| Repeated failed update retries burn battery | `lastFailedVersion` skips known-bad versions; daily cadence caps retry frequency anyway |
| Wrong partition scheme on first flash | Phase 0 checks this; Phase 3 won't work without 2-slot scheme |
| Secrets leaked via Actions logs | Use GitHub Actions secrets (masked in logs); never `echo` config.h contents |
| Release deleted / repo renamed | Manifest URL is what the device fetches — if that URL goes stale, device keeps running current firmware indefinitely. Safe failure mode. Don't delete releases the device might still pull |
| WiFi works on the bench but not in production (e.g. new release breaks WiFi credentials handling) | App-level rollback fires because `setup()` never reaches `markFirmwareValid()` — WiFi-failure paths must abort `setup()`, not loop-retry forever |
| CI `config.h` drifts from bench `config.h` | CI is the source of truth; bench should *read* the CI config to test what will actually ship. Document this in CLAUDE.md |

---

## Open questions

- Should `lastFailedVersion` ever get cleared automatically, or only by tagging a new version? Leaning toward "only by new tag" — if it failed, it failed, force a fix.
- Worth a "boot count" telemetry line in the footer for the first few weeks of OTA, so we can see if rollback is firing silently? Probably yes; remove later alongside `SHOW_VERSION_FOOTER`.

---

## Out of scope (deliberately not doing)

- Signed firmware updates (overkill for this threat model)
- Delta updates (1 MB full binary is fine)
- Multiple staged environments / canary releases (one device, one user)
- Remote logging back to a server (serial logs over USB cover all debugging needs)
- TLS certificate validation (already using `setInsecure()` everywhere; consistent with project posture)
- Custom bootloader with `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` (app-level rollback achieves the same outcome with much less complexity)
- Battery-threshold gating of OTA checks (no battery monitoring code exists today; not worth adding just for this)
