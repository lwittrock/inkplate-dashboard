# Tier 1 power-savings — implementation plans

Forward-looking work plans for the three Tier 1 recommendations from [power-audit.md](power-audit.md). Companion to that document, not a replacement. Each plan lists steps, refined savings, edge cases, and verification.

**Combined Tier 1 outcome:** ~11 mAh/day saved → daily total ~31.5 mAh → runtime ~30 days (vs ~22 baseline). Consistent with the corrected §9 summary table in [power-audit.md](power-audit.md).

---

## Decision: stage the rollout, don't bundle

All three are good changes. The reason to stage is **diagnostic ambiguity**, not safety per se — if all three go out together and the wall-mounted device misbehaves the next morning, you have three suspects and bisecting requires reverts. The OTA workflow is push-to-master → auto-tag → release, so three separate pushes cost nothing.

**Suggested order:**
1. Static IP (with DHCP fallback in the same commit) — config-only but with the only real wall-bricking failure mode in the set
2. Conditional GV trip fetch — biggest single win, isolated to network + picker
3. Open-Meteo split URLs + daily cache — most surface area, save for when the other two are quiet

Bundling #2 and #3 in one push is acceptable if speed matters; #1 stays on its own commit so the DHCP-fallback safety net is reviewable in isolation.

---

## 1. Static IP — `WIFI_STATIC_IP` and friends

**Goal:** skip DHCP on every wake.

### What already exists
[B_Network.ino:15-22](../B_Network.ino#L15-L22) already has the `#ifdef WIFI_STATIC_IP` block calling `WiFi.config(ip, gw, sn, dns)` before `WiFi.begin()`. The optional fields are also already documented (commented out) in [config.h.example](../config.h.example).

### Steps (sequencing matters — per CLAUDE.md "secret-before-push")
1. Reserve the IP at the router (outside the DHCP pool, or via MAC reservation).
2. Add DHCP-fallback retry in [B_Network.ino](../B_Network.ino) `initHardware()` — see "DHCP fallback" below.
3. Uncomment and fill the four `#define`s in local [config.h](../config.h) so the next USB-flash works.
4. Update the `CONFIG_H` GitHub Actions secret with the same four lines.
5. Push a commit touching firmware sources on master → CI auto-tags and releases.

### DHCP fallback (load-bearing — do not skip)
If the static IP becomes invalid (router subnet change, IP collision, typo), `WiFi.status()` never reaches `WL_CONNECTED` → `showError("WiFi Error") + goToSleep(600)` runs every 10 min forever. The firmware **boots fine** so app-level rollback never triggers, AND **OTA can't push a fix** because OTA needs WiFi. Recovery would require pulling the frame off the wall and USB-reflashing.

Mitigation: after the initial connect loop fails and `WIFI_STATIC_IP` was defined, reset to DHCP mode and retry the connect loop once before giving up. Converts the failure mode from "permanent brick" to "wakes 1–2 s slower" with OTA still functional.

Sketch (final form goes in `initHardware()` after the existing 40×500ms loop, before the `showError` block):
```c
#ifdef WIFI_STATIC_IP
if (WiFi.status() != WL_CONNECTED) {
  DBGLN("WiFi: static-IP attempt failed, retrying with DHCP");
  WiFi.disconnect(true);
  WiFi.config(IPAddress(0,0,0,0), IPAddress(0,0,0,0), IPAddress(0,0,0,0));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500);
    attempts++;
  }
}
#endif
```

### Refined savings
DHCP handshake is typically 200–800 ms on a healthy AP plus a 700–1500 ms association phase that runs either way. Bench-case can be as low as 0.5 s saved; contended 2.4 GHz can save 2.5 s.
- **Range: 2.5–4.5 mAh/day** (~2 extra days runtime).

### Verification
With `DEBUG_LOG=1`, compare boot → "WiFi connected" wall-clock before vs after. Should drop by ~1–2 s on average.

### Risks summary
- Wrong static IP → wall-brick *if* DHCP fallback isn't shipped. With fallback: 1-cycle slow boot.
- DNS choice: router (fast, cached) vs public (`1.1.1.1`, reliable). Router usually 50–200 ms faster.

---

## 2. Open-Meteo split URLs + daily cache

**Goal:** stop re-fetching the 7-day daily forecast every wake when it barely changes within a day.

### Current state
[B_Network.ino:115-231](../B_Network.ino#L115-L231) — single fetch returns `hourly.temperature_2m` (24 entries, genuinely fresh every wake — sparkline) AND `daily.*` (7 entries × ~10 fields, only really needs to change ~daily). Daily output consumed by:
- Week strip ([C_Display.ino:753](../C_Display.ino#L753))
- Sunrise/sunset in masthead arc ([C_Display.ino:294](../C_Display.ino#L294))
- Sunrise/sunset guides in rain chart ([C_Display.ino:496,994](../C_Display.ino#L496))

### Approach — split URLs (NOT "keep one URL, skip parsing")
Keep-one-URL only saves CPU parsing (<0.1 mAh/day). Not worth it. Real savings come from cutting the HTTP slurp + TLS handshake, which requires splitting:
- **Hourly-only URL** every wake: `?hourly=temperature_2m&forecast_hours=24` — payload ~2 KB, ~1–1.5 s.
- **Daily-only URL** every N hours (or at calendar rollover): `?daily=…&forecast_days=7` — payload ~3 KB, ~1.5 s.

### RTC cache struct
In [Dashboard.ino](../Dashboard.ino), next to `brCache`:
```c
struct OmDailyCache {
  uint32_t    magic;          // sentinel like otaRtcMagic
  time_t      fetchedAt;      // epoch of last successful daily snapshot
  int         count;
  DayForecast forecast[7];
};
RTC_DATA_ATTR OmDailyCache omCache = {};
```

### Refresh rule for daily cache
Re-fetch when **any** of:
- Magic mismatch (cold boot)
- `now - fetchedAt >= OM_DAILY_TTL_MIN * 60` (default 360 = 6 h)
- `localtime(now).tm_mday != localtime(fetchedAt).tm_mday` (calendar rollover — forces refresh at midnight so "Today" doesn't lag)

Otherwise serve cached values.

### Steps
1. Add `OM_DAILY_TTL_MIN` (default 360) to [config.h.example](../config.h.example).
2. Split URL constants or branch internally in `fetchOpenMeteo`. Single-function with internal branching is simpler.
3. Add `OmDailyCache omCache` `RTC_DATA_ATTR` in [Dashboard.ino](../Dashboard.ino) with magic sentinel.
4. In `setup()`, always call hourly-fetch; call daily-fetch only when stale; restore `forecast[]` from `omCache` either way.
5. On daily-fetch failure: fall back to cached values (same pattern as `brCache`).
6. Defensive: if `omCache` is empty AND daily fetch fails on cold boot, do a single combined fetch as a one-shot fallback so the week strip isn't blank.

### Refined savings
- Current OM: ~3 s × 110 mA per wake = 330 mAs/wake.
- New: ~1.5 s × 110 mA per wake + ~1.5 s × 110 mA × 4 calls/day daily fetches.
- Net save ≈ (3 − 1.5) × 110 × 68 / 3600 − (1.5 × 110 × 4 / 3600) ≈ **2.8 mAh/day**.
- **Range: 2.5–3 mAh/day** (~1–2 extra days runtime).

### Edge cases
- "Today" row at calendar rollover — `tm_mday` check handles it.
- Tomorrow's sunrise comes from `forecast[1]`; daily forecast covers 7 days ahead so cached data always has it.
- Open-Meteo free tier: 10k calls/day. Even at 4× daily + 68× hourly = 272/day, well within limits.
- OTA-induced RTC loss: magic mismatch → one daily refetch on first wake post-OTA. Negligible.

### Verification
- `DEBUG_LOG=1`: confirm "OM daily: cached (Nm old)" vs "OM daily: refreshed" appears at right cadence.
- Wait through a midnight rollover with the device awake — confirm "Today" row updates.

---

## 3. Conditional GV (HS) trip fetch

**Goal:** skip the second `fetchTrips` call when Centraal has no disruptions for the picker to substitute around.

### Current picker behaviour
From [A_Calculations.ino:151-229](../A_Calculations.ino#L151-L229):
- Fills 3 slots from CTR trips in order.
- For each CTR trip, if `cancelled || parseDelayMin(delay) >= 10`, looks for an HS substitute within ±20 min.
- `nCtr == 0` fallback: promotes HS to primary with note "Centraal unavailable".

HS data is only consumed when (a) `nCtr == 0`, or (b) at least one of the next ~5 CTR trips is bad. Routine commute days = entire GV fetch wasted (~3–4 s WiFi).

### User-facing requirement (preserved by this plan)
The dashboard must continue to show:
- **CTR cancellation** when it happens (current "strikethrough + bad indicators" UI)
- **HS substitution** when CTR is bad and HS has a viable alternative (current "from HS" note)
- **Bad CTR fallback** when CTR is bad and HS also has no viable alternative

The "always re-fetch GV when CTR shows any disruption" rule below is what guarantees this. The cache only ever serves data when CTR is fully clean — i.e. when the picker doesn't consult HS at all. **You can never miss a cancellation because of the cache.**

(Note: the *current* firmware already doesn't surface CTR-clean-but-GV-cancelled — picker shows CTR in that case. This plan preserves that behavior. Surfacing GV status independently would be a separate UI change.)

### RTC cache struct
```c
#define HS_CACHE_MAGIC 0xC0FFEE43UL   // bump if Departure struct layout changes!

struct HsTripCache {
  uint32_t  magic;
  time_t    fetchedAt;
  int       count;
  Departure trips[6];
};
RTC_DATA_ATTR HsTripCache hsCache = {};
```

### Decision logic in `setup()`
```text
fetch CTR
filter dominated CTR trips
inspect CTR: ctrHasDisruption(ctrRaw, nCtr)?
                — any of first min(5, nCtr) trips: cancelled OR delay >= 10
                — OR nCtr == 0

  if YES → fetch GV fresh
            ├─ on success: filter, overwrite cache, hand to picker
            └─ on FAILURE: fall back to hsCache if non-empty (log "fetch failed,
                           using cache"); else nHs = 0 (same as today)
  if NO  → cache valid (magic OK AND now - fetchedAt < HS_CACHE_TTL_MIN)?
            ├─ YES: serve cache directly to picker (no fetch)
            └─ NO:  fetch GV fresh, filter, overwrite cache, hand to picker
```

Key correctness invariants:
- Whenever picker needs HS data (CTR disrupted), we always *try* to fetch fresh first.
- Cache is never consulted when CTR is disrupted *unless* the fresh fetch fails — strictly better than today's "nHs=0 on fetch failure" behavior.
- On a clean-CTR wake, picker doesn't look at HS at all, so cache content doesn't matter for output.

### Cache TTL
**Default 45 min** (covers ~2 wakes with 15-min cadence → ~67% skip rate, matching the savings estimate). 30 min would only cover 1 wake (~50% skip), undershooting the target.

### What we explicitly do NOT do (decided overkill)
- Invalidate cache on `STATION_CODE_HS` / `STATION_CODE_DESTINATION` change — never happens in practice.
- Time-travel safety with `abs(now - fetchedAt)` — ESP32 RTC drift is <1 s/day, NTP corrections are sub-second.
- "Cache must have ≥ N future trips" rule — overspecified. Cache only matters when picker doesn't consult HS, so an empty cache on a clean-CTR wake is fine.

### Steps
1. Add `#ifndef HS_CACHE_TTL_MIN #define HS_CACHE_TTL_MIN 45 #endif` somewhere globally accessible. Optional `config.h.example` override.
2. Add `HsTripCache hsCache` `RTC_DATA_ATTR` + magic constant in [Dashboard.ino](../Dashboard.ino).
3. Add `bool ctrHasDisruption(const Departure[], int n)` helper in [A_Calculations.ino](../A_Calculations.ino).
4. Add `bool hsCacheValid()` helper (magic + TTL check).
5. Refactor GV fetch call site in [Dashboard.ino](../Dashboard.ino) `setup()` per the decision logic above. **Cache write happens AFTER `filterDominatedTrips` so we cache the post-filter list — smaller, matches picker view.**
6. Debug logging: `(cached, Nm old)` marker on the "HS raw" line when serving from cache, `(fetch failed, using cache)` marker on the disrupted-path fallback.

### Refined savings
- GV fetch ≈ 3 s × 110 mA = 330 mAs/wake when skipped.
- 45-min TTL ≈ 67 % skip rate on disruption-free days; disruption-free days assumed ~80 % of wakes.
- Effective skip rate ≈ 0.8 × 0.67 ≈ 53 % across all wakes (mixed days).
- Save ≈ 330 × 0.53 × 68 / 3600 ≈ **3.3 mAh/day** sustained, **4.7 mAh/day** on fully disruption-free days.
- **Range: 3–5 mAh/day** (~2 extra days runtime). Largest single Tier 1 win.

### Edge cases
- CTR fetch fails entirely (`nCtr == 0`) → counts as disruption → forces GV fetch. If GV also fails, fall back to cache (new behavior — improvement over today). Picker's "Centraal unavailable" branch then uses whatever HS data is available.
- Disruption appears mid-TTL → next wake's CTR sees it → re-fetches GV. Cache never serves data when picker actually needs substitution.
- Stale cached trips that have already departed → picker's `t < now + 5*60` guard filters them, naturally.
- OTA-induced RTC loss → magic mismatch → empty cache → one forced GV fetch. Negligible.

### CLAUDE.md addition required
Add a line to the "Working with Claude Code" section: **"If you add a field to the `Departure` struct, bump `HS_CACHE_MAGIC` in `Dashboard.ino`. The RTC cache reads bytes positionally; a layout change without a magic bump silently reads garbage into the new field."**

### Verification
- Force CTR-disruption appearance in a serial run (e.g. temporarily lower the delay threshold) and confirm GV fetch fires.
- Normal run: confirm `HS raw: N (cached, Nm old)` appears in serial.
- Spot-check picker slots remain correct when GV served from cache.
- (No-USB option:) just trust the OTA — if the dashboard keeps updating and substitutions still appear during real disruptions, it works.

---

## Summary table

| # | Change | Effort | Daily save | Cum. runtime |
|---|---|---|---|---|
| baseline | — | — | 0 mAh | ~22 days |
| 1 | Static IP + DHCP fallback | S | 2.5–4.5 mAh | ~24 days |
| 3 | Conditional GV fetch (45 min TTL) | M | 3–5 mAh | ~26 days |
| 2 | OM split URLs + daily cache | M | 2.5–3 mAh | ~30 days |

(Order in this table matches the suggested rollout order, not the audit's numbering.)
