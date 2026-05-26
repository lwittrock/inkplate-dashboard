# Power-Usage Audit — Inkplate6V2 Dashboard

> **STATUS — 2026-05-26: All Tier 1 recommendations shipped.** Static IP (commit `fe7ef5e`), conditional GV trip fetch (commit `35b7a04`), and Open-Meteo split URLs + 6h daily cache (commit `6a67cbc`) are live in production. Modelled daily draw drops from ~42.5 mAh/day → **~30–34 mAh/day**; runtime projection **~28–34 days** (vs ~22 baseline). The audit below describes the **pre-Tier-1 baseline** — kept as historical reference. See §9 for which recommendations were taken.

---

**Audit date:** 2026-05-25
**Firmware state audited:** `master` at commit `144d3e7` (pre-Tier-1)
**Battery modelled:** KW-2152 LiPo, 3.7 V nominal, 1200 mAh (≈ 4.44 Wh nominal, ≈ 960 mAh usable assuming 80% depth-of-discharge on a LiPo)
**Method:** code-path trace (firmware as it existed at audit time) + datasheet currents from Espressif and Soldered. **No hardware measurements were taken** — every duration and current below is a modelled estimate. The point of this report is to identify *where* the energy goes, not to certify *exactly how much*.

**Config baseline:** the production device runs CI-built binaries whose `config.h` comes from the GitHub `CONFIG_H` secret, **not** the local [config.h](../config.h) in this repo. Per the user, the deployed secret has `OTA_TEST_FORCE_CHECK` turned **off** (i.e., the once-per-day throttle is active). The local file still has it on, but that only matters for USB flashes. Numbers below model the production case.

---

## 1. Executive summary

At today's production settings (OTA force-check OFF, daily throttle active) the dashboard burns an estimated **≈ 42 mAh per day**, projecting **≈ 22–23 days** of runtime on a fully charged 1200 mAh battery. With the forced OTA check enabled (the local-config case) the figure rises to ~45 mAh/day → ~21 days. The breakdown is overwhelmingly lopsided:

| Bucket | Share of daily mAh | Why |
|---|---|---|
| **WiFi active** (connect + fetches + OTA manifest) | **~93 %** | ~17 s of radio-on per wake × 68 wakes/day at ~100–120 mA |
| CPU active for render @ 80 MHz | ~3 % | ~3 s/wake at ~25 mA |
| Deep-sleep quiescent (24 h × 25 µA) | ~1.5 % | Tiny current × huge time |
| E-ink refresh (full + partial mix) | ~1.5 % | Sub-second panel pulses |
| Boot/setup overhead | ~1 % | `display.begin`, OTA RTC state |

**The one finding that matters: WiFi-active time per wake is the only knob that meaningfully moves the daily budget.** Everything else combined is <10 %. Notably, two current `config.h` settings are inflating that WiFi window beyond what production-mode operation needs — see §6.

---

## 2. Hardware power budget (datasheet ground truth)

| State | Current draw | Source / confidence |
|---|---|---|
| Inkplate6 whole-board deep sleep | **22–25 µA** | [Soldered Inkplate docs](https://docs.soldered.com/inkplate/2/low-power/deep-sleep/) — vendor-published |
| ESP32 deep sleep (chip only, RTC timer) | ~10 µA | Espressif ESP32 datasheet — vendor-published |
| ESP32 active, CPU 240 MHz, radio idle | ~30–50 mA | [Espressif current-measurement guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/current-consumption-measurement-modules.html) |
| ESP32 active, CPU 80 MHz, radio off | ~15–25 mA | Espressif — scales sub-linearly with clock |
| WiFi RX (associated, listening) | **95–100 mA** | Espressif |
| WiFi TX peak (TLS handshake, retransmits) | **180–240 mA** | Espressif — short bursts, not sustained |
| WiFi connect + DHCP burst (averaged) | ~120 mA over 2–4 s | Derived — mix of RX/TX/handshake |
| HTTPS GET in steady state (averaged) | ~100–110 mA | Derived — mostly RX-dominated after handshake |
| ED060SC7 panel during full refresh | ~40–80 mA peak for ~1–2 s | **Inferred** — Soldered does not publish; community measurements vary |
| ED060SC7 panel during partial update | ~30–50 mA for ~100–300 ms | **Inferred** — same caveat |
| Onboard 3.3 V regulator quiescent | ~15–20 µA | Already included in the 22–25 µA whole-board sleep figure |

**Reconciliation note:** [CLAUDE.md](../CLAUDE.md) records the deep-sleep floor as "30–40 µA" from observation; Soldered's published figure is 22–25 µA. The difference (≈ 15 µA) is plausibly some combination of LiPo charge-circuit leakage, the level-shifter chain to the e-ink panel, and measurement-instrument burden. **It doesn't matter for this report**: even at 40 µA, the deep-sleep bucket only grows from ~1.5 % to ~2 % of the daily budget. The whole quiescent line is small.

---

## 3. Per-phase timing model (one normal wake)

Each phase is taken from the code trace done in Phase 1. The "Current" column is the **average** drawn during that phase (TX peaks are real but short, so a 100–120 mA average for WiFi work is the right modelling figure for energy purposes).

| # | Phase | Code reference | Time | Current | Charge (mAs) |
|---|---|---|---|---|---|
| 1 | Wake + `display.begin()` + OTA RTC check | [Dashboard.ino:154-172](../Dashboard.ino#L154-L172) | 0.5 s | 50 mA | 25 |
| 2 | Serial init + 200 ms delay (DEBUG_LOG=1) | [Dashboard.ino:161-163](../Dashboard.ino#L161-L163) | 0.2 s | 50 mA | 10 |
| 3 | WiFi connect (DHCP, no static IP set) | [B_Network.ino:24-30](../B_Network.ino#L24-L30) | 3 s | 120 mA | 360 |
| 4 | NTP sync (amortised, runs 1 wake in 4) | [B_Network.ino:44-58](../B_Network.ino#L44-L58) | 0.5 s avg | 100 mA | 50 |
| 5 | Open-Meteo fetch (~20 KB) | [B_Network.ino:115-231](../B_Network.ino#L115-L231) | 3 s | 110 mA | 330 |
| 6 | Buienradar feed (~50 KB, consensus picker) | [B_Network.ino:276-437](../B_Network.ino#L276-L437) | 4 s | 110 mA | 440 |
| 7 | Buienradar rain nowcast (~8 KB) | [B_Network.ino:443-493](../B_Network.ino#L443-L493) | 2 s | 110 mA | 220 |
| 8 | NS Trip Planner ×2 (~90 KB each) | [B_Network.ino:500-645](../B_Network.ino#L500-L645) | 6 s | 110 mA | 660 |
| 9 | OTA manifest (production: ~1×/day, amortised per wake) | [D_OTA.ino:145-190](../D_OTA.ino#L145-L190) | 0.02 s avg | 110 mA | ~2.5 |
| 10 | `WiFi.disconnect(true)` + `WIFI_OFF` | [Dashboard.ino:288-289](../Dashboard.ino#L288-L289) | <0.1 s | 30 mA | 3 |
| 11 | `setCpuFrequencyMhz(80)` + render | [C_Display.ino:990-1100](../C_Display.ino#L990-L1100) | 3 s | 25 mA | 75 |
| 12 | E-ink refresh (full ¼ + partial ¾, amortised) | [C_Display.ino:1106-1112](../C_Display.ino#L1106-L1112) | 0.5 s avg | 60 mA avg | 30 |
| 13 | `goToSleep()` entry | [B_Network.ino:86-93](../B_Network.ino#L86-L93) | <0.1 s | 30 mA | 3 |
| 14 | Deep sleep 899 s | — | 899 s | 0.025 mA | 22.5 |
| | **Per-wake total (production)** | | **~22 s active + 899 s sleep** | | **~2233 mAs ≈ 0.620 mAh** |
| | Per-wake total (local config, force-check on) | | ~24 s active | | ~2395 mAs ≈ 0.665 mAh |

**Sensitivity on the dominant lever:** rows 3–9 sum to **~2230 mAs (93 % of one wake)**. Anything that shortens the WiFi window scales the daily budget almost linearly. Phases 1, 2, 11, 12, 13 combined are ~145 mAs (~6 %). Deep sleep is ~22 mAs (~1 %).

---

## 4. Day-in-the-life budget

**Schedule:**
- Daytime: 17 h × 4 wakes/h = **68 normal wakes**
- Night: one wake at 23:30 brings WiFi up to run the OTA manifest check, then sleeps in one block to 06:30
- Full e-ink refresh: every 4th wake (~once an hour), per `FULL_REFRESH_EVERY = 4`
- NTP resync: every 60 min, per `NTP_RESYNC_MIN = 60`
- OTA binary download: rare; modelled separately at end

**Daily charge:**

| Bucket | Per-day charge | Notes |
|---|---|---|
| 68 normal wakes × 0.620 mAh (production) | **~42.2 mAh** | From §3 |
| 1 night-entry wake (WiFi up, OTA manifest, no display, 7 h sleep) | ~0.34 mAh | WiFi ~5 s + 7 h × 25 µA |
| **Daily total (production)** | **~42.5 mAh/day** | OTA force-check OFF |
| Daily total if force-check were ON | ~45.5 mAh/day | +3 mAh/day, matches CLAUDE.md's "~10%" estimate |
| Usable battery (1200 mAh × 0.8) | 960 mAh | LiPo cutoff |
| **Projected runtime** | **≈ 22–23 days** | 80% confidence: 18–28 days, depending mostly on WiFi-window variance |

**Sub-bucket breakdown (per day):**

| Bucket | mAh/day | Share |
|---|---|---|
| WiFi active (connect + 5 fetches; OTA manifest 1×/day) | **~39 mAh** | **~92 %** |
| CPU render @ 80 MHz | ~1.4 mAh | ~3 % |
| Deep-sleep quiescent (24 h × 25 µA) | ~0.6 mAh | ~1.3 % |
| E-ink refresh (amortised full+partial) | ~0.6 mAh | ~1.3 % |
| Boot setup, Serial init, sleep entry | ~0.5 mAh | ~1 % |
| Night-entry wake overhead | ~0.3 mAh | ~0.7 % |

---

## 5. Where the energy actually goes — ranked

> **Conversion note:** the values below are per-wake mAs (from §3) × 68 wakes/day ÷ 3600 = mAh/day. An earlier revision of this section dropped the /3600 factor on several lines and inflated the WiFi-active buckets by ~3.6×; the numbers here reconcile cleanly with §3 and §4's 42.5 mAh/day total.

1. **NS Trip Planner ×2 — 660 mAs × 68 / 3600 ≈ 12.5 mAh/day (~29 %)**
   The two `fetchTrips` calls in [B_Network.ino:500-645](../B_Network.ino#L500-L645) are the single largest line item: ~6 s of radio-on for ~180 KB total slurped over TLS. Both are necessary (GVC + GV) because the per-slot picker can substitute HS departures when a Centraal slot is disrupted — so this is doing real work. (`filterDominatedTrips` runs after each fetch on the parsed tree only — pure CPU, negligible.)

2. **Buienradar feed (consensus picker) — 440 mAs × 68 / 3600 ≈ 8.3 mAh/day (~20 %)**
   [B_Network.ino:276-437](../B_Network.ino#L276-L437). One HTTP call downloads the full national station feed (~50 KB) and a local in-memory consensus vote runs over the nearest 6 stations. The cost is in the download size, not the algorithm.

3. **WiFi connect (DHCP) — 360 mAs × 68 / 3600 ≈ 6.8 mAh/day (~16 %)**
   [B_Network.ino:24-30](../B_Network.ino#L24-L30). No static IP is configured (`WIFI_STATIC_IP` not defined in [config.h](../config.h)), so DHCP runs every wake. CLAUDE.md notes static IP saves "~1–2 s DHCP per wake."

4. **Open-Meteo forecast — 330 mAs × 68 / 3600 ≈ 6.2 mAh/day (~15 %)**
   [B_Network.ino:115-231](../B_Network.ino#L115-L231). Hourly + daily forecast slurp, ~3 s.

5. **Buienradar rain nowcast — 220 mAs × 68 / 3600 ≈ 4.2 mAh/day (~10 %)**
   [B_Network.ino:443-493](../B_Network.ino#L443-L493). Small payload but still pays the TLS handshake.

6. **CPU render @ 80 MHz — 75 mAs × 68 / 3600 ≈ 1.4 mAh/day (~3 %)**
   [C_Display.ino:990-1100](../C_Display.ino#L990-L1100). Font rendering + bitmap blits to the in-memory framebuffer. CPU clock drop at [Dashboard.ino:293](../Dashboard.ino#L293) is doing its job here.

7. **NTP resync (amortised) — ~0.9 mAh/day (~2 %)**
   [B_Network.ino:44-58](../B_Network.ino#L44-L58). Once-per-hour blocking SNTP; the other 3 of 4 wakes just `setenv("TZ")` and skip the radio.

8. **Boot/setup overhead — ~0.8 mAh/day (~2 %)**
   `display.begin()`, OTA RTC check, Serial init (200 ms delay when `DEBUG_LOG=1`), sleep entry. Phases 1+2+10+13 in §3.

9. **Deep-sleep quiescent — 24 h × 25 µA ≈ 0.6 mAh/day (~1.4 %)**
   Already at the practical floor for this board. CLAUDE.md correctly lists "sleep current optimization" as consciously skipped.

10. **E-ink refresh (full + partial) — 30 mAs × 68 / 3600 ≈ 0.6 mAh/day (~1.4 %)**
    [C_Display.ino:1106-1112](../C_Display.ino#L1106-L1112). 17 full refreshes/day + 51 partials. Surprisingly small relative to common assumptions about e-ink "free" updates being expensive — they aren't free, but the panel is only powered briefly.

11. **OTA manifest fetch — ~0.05 mAh/day in production (negligible)**
    [D_OTA.ino:145-190](../D_OTA.ino#L145-L190). Production is *assumed* to have `OTA_TEST_FORCE_CHECK = 0` in the GitHub `CONFIG_H` secret (per the user — not verified from this repo, since the secret can't be inspected), so the per-day throttle is active and the manifest fires once per day on the first wake past midnight. If the local-config (force-on) build were flashed via USB, this line would grow to ~3 mAh/day — CLAUDE.md flags this as "~10 % battery cost".

12. **OTA binary downloads — ~1–3 mAh per occurrence**
    [D_OTA.ino:92-114](../D_OTA.ino#L92-L114). ~1.2 MB binary at ~100 mA for 30–120 s. Amortised over release cadence (say 1/week → ~0.3 mAh/day; 1/month → ~0.07 mAh/day). Negligible at any realistic release frequency.

**Reconciliation:** lines 1–11 sum to ~42.4 mAh/day, matching §4's daily total to within rounding. The WiFi-active buckets (1–5 + 7) account for ~39 mAh/day, ~92 % of the budget — consistent with §1.

---

## 6. Findings the audit surfaced

These came out of the code trace and aren't proposals — just facts that materially affect the model:

- **`OTA_TEST_FORCE_CHECK` is `1` locally but `0` in the production `CONFIG_H` GitHub secret.** Production is already paying the cheap (daily-throttled) path. The local file is misleading if read in isolation — worth syncing the local copy so future audits don't model the wrong case.
- **`DEBUG_LOG = 1`** in [config.h:54](../config.h#L54) costs ~0.7 mAh/day (the 200 ms `Serial.begin` delay at [Dashboard.ino:161-163](../Dashboard.ino#L161-L163), plus serial I/O overhead during fetches). Small but free to recover.
- **No static IP defined.** CLAUDE.md mentions `WIFI_STATIC_IP` as an option but [config.h](../config.h) doesn't define it. DHCP adds an estimated ~1.5 s × 120 mA = 180 mAs per wake; 180 × 68 / 3600 ≈ **3.4 mAh/day**.
- **Trip Planner is the single largest fetch.** Not a problem — it's doing necessary work — but it is the natural ceiling on what's achievable without changing data sources.
- **All five HTTPS calls open fresh TLS sessions** (no session reuse) — see [B_Network.ino:124,147,281,447,549](../B_Network.ino#L124). Each handshake is one of the 100–240 mA peaks on the WiFi power trace.
- **`setInsecure()` on every call** ([B_Network.ino:124,147,281,447](../B_Network.ino#L124)) saves a small amount of handshake CPU/radio vs full cert validation — already mentioned as a project posture in CLAUDE.md, listed here for completeness.
- **Bluetooth is explicitly stopped at boot** ([Dashboard.ino:157-158](../Dashboard.ino#L157-L158)). Confirmed — no BT contribution to model.
- **Deep sleep night block works as designed.** The 23:30 → 06:30 single sleep is the largest energy-saving feature in the firmware. Without night mode, 28 additional wakes × ~0.665 mAh = ~18.6 mAh/night, which would push daily total to ~64 mAh and runtime down to ~15 days.

---

## 7. Caveats and soft numbers (read before quoting any figure)

- **TLS handshake duration is highly variable.** A single retry on a flaky AP could double a fetch's duration. The 110 mA average is a rough mean over the connect/handshake/RX mix.
- **ED060SC7 panel current during refresh is not published by Soldered.** The 40–80 mA / 30–50 mA figures are community estimates; the e-ink contribution could easily be 2× either way and the daily total would barely move (it's only 1.3 %).
- **WiFi connect time of 3 s is an estimate** — the code allows up to 20 s. A weak signal can blow this up significantly. The most impactful variable in the whole model.
- **NTP amortisation assumes the cached-NTP path works as designed.** A genuine cold boot pays the full 15 s timeout once.
- **DHCP estimate (~1.5 s)** is router-dependent. Some routers reply in <500 ms; some take 3+ s.
- **Battery usable capacity (960 mAh) assumes 80 % DoD.** A LiPo run flatter will give more capacity but shortens cycle life. A conservative 70 % DoD would give ~18 days runtime instead of ~21.
- **No measurement validation has been done.** This entire report is an analytical model. A USB power meter or INA219 in series with the battery would let you validate phase 3's per-phase numbers in ~30 minutes of bench time.

---

## 8. How to verify this report against reality

Since no code changed, "verification" means sanity-checking the model:

1. **Wall-clock comparison.** With `DEBUG_LOG=1` (already enabled), look at the serial log of one wake and measure: time from boot to "WiFi connected", time per fetch, total time from boot to `goToSleep`. Compare to the §3 timings — if your actual WiFi-up window is materially longer than 17 s, the daily total scales up proportionally.
2. **Battery-life observation.** When you next charge from empty, note the date. If runtime is 18–24 days, the ~45 mAh/day model is essentially right. If it's <14 days, there's a hidden energy sink the model missed (most likely culprit: WiFi connect repeatedly hitting the 20 s timeout on a weak signal).
3. **USB power meter, if available.** Connecting a meter (a $10 inline USB-A meter is plenty for ±5 % accuracy) between USB and the Inkplate during a normal wake will show the per-phase current curve directly. The §3 table tells you what each phase looks like.
4. **DEBUG_LOG in production?** Check whether the `CONFIG_H` GitHub secret also has `DEBUG_LOG = 1`. If yes, the Serial init + 200 ms delay are paid on every production wake (~0.7 mAh/day). If the secret has it off, ignore that line item.

---

## 9. Recommendations (ranked by expected daily savings)

Estimates are best-effort and bracketed to reflect the soft numbers in §7. "Effort" tags: **S** = config-only change, **M** = small code edit + retest, **L** = real refactor or new caching subsystem.

### Tier 1 — biggest wins for the effort

> **Status: all three shipped on 2026-05-26 (commits `fe7ef5e`, `35b7a04`, `6a67cbc`).** Descriptions retained for context.

1. **Define `WIFI_STATIC_IP` / `GATEWAY` / `SUBNET` / `DNS` in `CONFIG_H` secret. (S)** ✅ Shipped — `fe7ef5e`. Includes a DHCP-fallback retry so a stale static IP can't brick the wall-mounted device beyond OTA recovery.
   *Saves ~3–4.5 mAh/day → ~2 extra days runtime.*
   [B_Network.ino:15-21](../B_Network.ino#L15-L21) already supports it; just unused. Eliminates DHCP round-trip (~1.5–2 s × ~120 mA per wake × 68 wakes/day). Caveat: if router IP pool changes or device moves networks, you'll need to update the secret + retag — but for a wall-mounted always-on-same-network device the trade-off is heavily in favour. (Cheapest config-only change in the whole list; ranking #1 stands even at the corrected smaller magnitude.)

2. **Make a stale-but-fresh-enough cache for Open-Meteo's 7-day daily forecast. (M)** ✅ Shipped — `6a67cbc`. Took the "split URLs" path: hourly fetched every wake, daily cached in RTC with 6 h TTL + calendar-rollover refresh.
   *Saves ~2–3 mAh/day → ~1–2 extra days runtime.*
   The hourly 24 h temps need to be fresh every wake (sparkline at the top of the hour). The **7-day daily forecast** doesn't — it barely changes within a day. Two paths:
   - **Easy:** keep the existing single fetch but refresh the daily portion only every 4–6 h via an `RTC_DATA_ATTR omCache` (like the existing `brCache`). Hourly stays per-wake.
   - **Cleaner:** split into two Open-Meteo URLs — hourly-only every wake (~10 KB, ~1.5 s), daily-only every 6 h (~5 KB). Net: ~1.5 s saved on 3 of 4 wakes ≈ 0.75 × 1.5 × 110 mA × 68 / 3600 ≈ 2.3 mAh/day.

3. **Drop one of the two Trip Planner calls when one origin is clearly disrupted. (M)** ✅ Shipped — `35b7a04`. RTC-cached GV result with 45 min TTL, served only when CTR is clean; any CTR disruption forces a fresh GV fetch so substitutions never see stale data.
   *Saves ~3–5 mAh/day depending on disruption rate.*
   Today both GVC→TBU and GV→TBU fetch unconditionally on every wake (~6 s combined). The HS leg's whole job is to be a fallback per slot. Idea: cache the GV result for ~30 min when GVC came back clean (no disrupted slots), and only refetch GV when GVC actually surfaces a disruption. On a normal undisrupted commute day that's 3 GV calls/day instead of 68 → ~3 s × 110 mA × ~50 wakes / 3600 ≈ 4.6 mAh/day. Risk: a fresh disruption between cached GV fetches could let one bad slot through — pick the cache TTL based on how often NS surfaces disruptions in practice.

### Tier 2 — modest wins, low risk

4. **Set `DEBUG_LOG = 0` in production `CONFIG_H` secret. (S)**
   *Saves ~0.7 mAh/day.*
   Removes 200 ms Serial init delay + serial I/O overhead per wake. Confirm whether the secret currently has it on or off (see §8 spot-check #4) before counting this saving.

5. **Skip OTA manifest on weekends or when on battery > 80 %. (S–M)**
   *Saves ~0.04 mAh/day.* Not really worth doing — manifest cost is already negligible in production (~0.07 mAh/day). Listed only to argue *against* further OTA optimization.

6. **Increase `FULL_REFRESH_EVERY` from 4 to 8. (S)**
   *Saves ~0.3 mAh/day.*
   Halves the full-refresh count from 17/day to ~8/day. Trade-off: more ghosting accumulation between full refreshes — visible on this Bayer-dithered layout. Not recommended unless you see the e-ink ghosting acceptable, but it's the cheapest knob if every mAh counts.

### Tier 3 — high effort, real risk, hold unless needed

7. **TLS session resumption / connection reuse across fetches. (L)**
   *Potential savings: ~2–4 mAh/day.*
   Each of the 5 HTTPS calls currently opens a fresh TLS session. Reusing a `WiFiClientSecure` across same-host calls (Buienradar feed + rain are on different hosts, NS is one host, Open-Meteo is one host) could trim ~200–500 ms per handshake. Real complexity though: `setInsecure()` quirks, `ArduinoJson` ownership, retry logic. Not worth it unless Tier 1+2 aren't enough.

8. **Switch from Open-Meteo + Buienradar to a single combined source. (L)**
   *Potential savings: ~5–10 mAh/day.*
   Eliminates one TLS handshake + one slurp. But the consensus picker, hourly sparkline, and 7-day strip are all hand-tuned to current data shapes. Big refactor for a real but bounded win.

9. **Move to 30-min wake interval instead of 15-min. (S, but UX cost)**
   *Saves ~21 mAh/day → roughly doubles runtime.*
   This is the nuclear option — fewest wakes, biggest win. Cost: rain nowcast becomes 30-min stale (problematic — 2 h rain prediction is the whole point), and the "Updated HH:MM" timestamp can be off by up to 30 min. Listed here so you can see the relative size of the gain compared to other levers.

### Things to NOT do (already optimal or hard ceilings)

These are recapped from CLAUDE.md's "consciously skipped" list — the audit confirmed they remain correct:
- **Sleep current optimization.** Already at the 22–40 µA floor.
- **Sub-80 MHz CPU during render.** WiFi instability risk; CPU render is only 3 % of daily budget anyway.
- **TLS cert pinning.** Tiny power win, big maintenance cost.
- **Region-targeted partial refresh.** Ghosting incompatible with Bayer-dithered fills.
- **Bluetooth.** Already off at boot.

### Summary table

| # | Change | Tier | Effort | Daily save | Cum. runtime |
|---|---|---|---|---|---|
| baseline | — | — | — | 0 mAh | ~22 days |
| 1 | Static IP | 1 | S | ~4 mAh | ~24 days |
| 2 | OM daily-forecast cache | 1 | M | ~2.5 mAh | ~26 days |
| 3 | Conditional GV trip fetch | 1 | M | ~4.5 mAh | ~30 days |
| 4 | DEBUG_LOG=0 in prod | 2 | S | ~0.7 mAh | ~30 days |
| 6 | FULL_REFRESH_EVERY=8 | 2 | S | ~0.3 mAh | ~31 days |

Doing all of Tier 1 takes you from **22 days → ~30 days** of runtime — a ~35 % improvement without touching the 15-min wake cadence or any hardware. After Tier 1, the dominant remaining cost is still WiFi (just less of it), and you'd be in diminishing-returns territory.

> **Note on earlier numbers.** A prior revision of this section claimed Tier 1 would take runtime from 22 → 42 days. That was based on §5 line items that had dropped the mAs→mAh /3600 conversion factor, inflating WiFi-active savings by ~3–4×. The ranking of recommendations is unchanged (static IP is still the cheapest #1, OM cache and conditional GV trip are still worth doing), but the absolute magnitudes have been corrected.

---

## 10. Data sources

- [Soldered Inkplate documentation — deep sleep](https://docs.soldered.com/inkplate/2/low-power/deep-sleep/)
- [Espressif ESP-IDF — Current Consumption Measurement of Modules](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/current-consumption-measurement-modules.html)
- Project [CLAUDE.md](../CLAUDE.md) — battery/optimization context, "consciously skipped" list
- Phase-1 firmware trace — file:line references throughout
