// ============================================================================
// INKPLATE WEATHER & TRAIN DASHBOARD
// ============================================================================
// Main file - defines data structures and program flow

#include "Inkplate.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <esp_bt.h>
#include "config.h"
#include "icons.h"

// Firmware version: CI writes firmware_version.h at build time (see
// .github/workflows/release.yml). Local builds without that header
// fall through to "dev" so the sketch still compiles in Arduino IDE.
#if __has_include("firmware_version.h")
  #include "firmware_version.h"
#endif
#ifndef FIRMWARE_VERSION
  #define FIRMWARE_VERSION "dev"
#endif

// ============================================================================
// DEBUG LOGGING
// ============================================================================
// Define DEBUG_LOG=1 in config.h to enable Serial output. Default off saves
// ~1 s wake time (no Serial.begin delay) and avoids UART tx blocking.

#ifndef DEBUG_LOG
#define DEBUG_LOG 0
#endif

#if DEBUG_LOG
  #define DBG(...)   Serial.print(__VA_ARGS__)
  #define DBGLN(...) Serial.println(__VA_ARGS__)
#else
  #define DBG(...)   ((void)0)
  #define DBGLN(...) ((void)0)
#endif

// ============================================================================
// DATA STRUCTURES
// ============================================================================

// Weather category enumeration
typedef enum {
  CLEAR,
  PARTLY_CLOUDY,
  OVERCAST,
  FOG,
  DRIZZLE,
  RAIN,
  RAIN_HEAVY,
  SNOW,
  THUNDERSTORM
} WeatherCategory;

// Which Den Haag station a trip departs from.
typedef enum { ORIGIN_CTR, ORIGIN_HS } TrainOrigin;

// Status of the transfer at Breda → Tilburg Universiteit.
typedef enum { TRANSFER_OK, TRANSFER_LATE, TRANSFER_CANCELLED } TransferStatus;

// One picked trip for display, fed by fetchTrips (NS Trip Planner v3) and
// the per-slot picker (A_Calculations.ino). Carries origin tag, transfer
// status, and Uni arrival so a single struct drives the card layout.
struct Departure {
  TrainOrigin origin;
  char time[6];                  // "HH:MM" actual departure (falls back to planned)
  char track[6];                 // "12a"
  char delay[8];                 // "+12m" or ""
  bool cancelled;
  char uniArr[6];                // "HH:MM" arrival at TBU
  TransferStatus transfer;
  char note[32];                 // reserved (formerly: "Centraal cancelled" overlay; dropped — HS pill is the signal)
  char plannedDepartureISO[26];  // "2026-05-24T12:19:00+0200" — for cross-origin sort
  uint8_t legCount;              // NS Trip Planner legs.size(); picker rejects HS substitutes with legCount > 2
};

// Crossed-concern bag used by the renderer. `windDirection` is populated
// by Buienradar (fetchBuienradarNow — the live observation source);
// `hourlyTemp` / `hourlyCount` are populated by Open-Meteo's hourly
// fetch (fetchOpenMeteoHourly). Bundled here because the renderer wants
// both alongside the current-weather temp/wind/code primaries.
struct WeatherExtras {
  int   windDirection;    // degrees from N, 0-360 (Buienradar)
  float hourlyTemp[24];   // next 24h starting from current hour (Open-Meteo)
  int   hourlyCount;      // number of hourly entries actually filled
};

// Daily weather forecast (calculated from API data)
struct DayForecast {
  char dayName[8];           // "Today" / "Mon" etc.
  int tempMax;
  int tempMin;
  int rainProb;              // Keep for display percentage
  WeatherCategory category;  // Calculated category (not raw WMO code)
  bool useSunnyVariant;      // Whether to use sun+rain/snow icon variant
  char sunrise[6];           // "HH:MM"
  char sunset[6];            // "HH:MM"
};

// ============================================================================
// GLOBAL OBJECTS
// ============================================================================

Inkplate display(INKPLATE_1BIT);

// RTC RAM survives deep sleep. Used to count wakes so we can do a full
// e-ink refresh once an hour and partial refreshes the rest of the time.
// Counter is 0 on cold boot — the cold boot then renders a full refresh.
RTC_DATA_ATTR uint32_t wakeCounter = 0;

// Last successful NTP sync (epoch seconds). The ESP32 RTC keeps running
// across deep sleep and drifts < 1 s/day, so HH:MM stays correct without
// resyncing on every 15-min wake. initHardware() only triggers a new SNTP
// when this is older than NTP_RESYNC_MIN, saving ~2–4 s of radio-on time
// on the 3 of 4 wakes that don't need it.
RTC_DATA_ATTR time_t lastNtpSync = 0;

// Last-known-good Buienradar values, persisted across deep sleep. If a
// fetch fails on a given wake we render from cache rather than going back
// to Open-Meteo or showing an empty section. nowValid / rainValid are
// independent — a 250-byte raintext failing does not invalidate the
// 5 current-conditions fields, and vice versa.
struct BuienradarCache {
  bool            nowValid;
  float           temp;
  WeatherCategory category;
  float           wind;
  int             windBearing;

  bool  rainValid;
  int   rainCount;
  float rainMmh[24];
  char  rainLabels[24][6];   // "HH:MM\0"
};
RTC_DATA_ATTR BuienradarCache brCache = {};

// Force a full refresh every Nth wake to clear ghosting from partial updates.
// With SLEEP_DURATION=900 (15 min), 4 = once per hour.
#ifndef FULL_REFRESH_EVERY
#define FULL_REFRESH_EVERY 4
#endif

// Minutes between blocking NTP resyncs. See config.h.example for details.
#ifndef NTP_RESYNC_MIN
#define NTP_RESYNC_MIN 60
#endif

// Cached-GV (HS) Trip Planner result, persisted across deep sleep. Only
// served to the picker when Centraal looks clean (no disruptions in the
// next 5 trips) — any CTR disruption forces a fresh GV fetch so the
// substitution logic always works on fresh data. See docs/tier1-
// implementation-plans.md §3 for the design.
//
// IMPORTANT: bump HS_CACHE_MAGIC if you change the layout of the Departure
// struct. The cache reads bytes positionally — a layout change without a
// magic bump silently reads garbage into the new field.
#ifndef HS_CACHE_TTL_MIN
#define HS_CACHE_TTL_MIN 45
#endif
// Magic sequence so each RTC cache has a distinct sentinel:
//   OTA_RTC_MAGIC   = 0xC0FFEE42 (D_OTA.ino)
//   HS_CACHE_MAGIC  = 0xC0FFEE45 (bumped from 43 when legCount field added)
//   OM_DAILY_MAGIC  = 0xC0FFEE44 (below)
// Keep them distinct so a future "bump on layout change" is unambiguous.
#define HS_CACHE_MAGIC 0xC0FFEE45UL

struct HsTripCache {
  uint32_t  magic;
  time_t    fetchedAt;
  int       count;
  Departure trips[6];
};
RTC_DATA_ATTR HsTripCache hsCache = {};

// Cached Open-Meteo daily forecast (7-day strip + sunrise/sunset).
// The hourly portion changes every wake (sparkline rolls forward an hour)
// so hourly is fetched fresh every cycle. The daily portion barely moves
// within a day, so it's cached and refreshed every OM_DAILY_TTL_MIN
// minutes OR at calendar-day rollover (so "Today" stays today). See
// docs/tier1-implementation-plans.md §2.
//
// IMPORTANT: bump OM_DAILY_MAGIC if you change the layout of DayForecast.
#ifndef OM_DAILY_TTL_MIN
#define OM_DAILY_TTL_MIN 360
#endif
#define OM_DAILY_MAGIC 0xC0FFEE44UL

struct OmDailyCache {
  uint32_t    magic;
  time_t      fetchedAt;
  int         count;
  DayForecast forecast[7];
};
RTC_DATA_ATTR OmDailyCache omDailyCache = {};

// ============================================================================
// MAIN EXECUTION
// ============================================================================

void setup() {
  // Power down radios we don't use. Bluetooth controller stays initialized
  // by default on ESP32-Arduino and leaks power.
  btStop();
  esp_bt_controller_disable();

#if DEBUG_LOG
  Serial.begin(115200);
  delay(200);
#endif
  DBGLN("\n\n=== DASHBOARD v2 (editorial redesign) ===");
  DBG("FW: "); DBGLN(FIRMWARE_VERSION);
  DBG("Wake #"); DBGLN(wakeCounter);

  display.begin();
  DBGLN("Display initialized");

  initOtaState();      // reset OTA RTC state if cold boot (sentinel mismatch)
  checkBootAttempts(); // may rollback + restart if pending OTA failed too often

  initHardware();
  DBGLN("Hardware initialized");

  // Reaching here means WiFi + NTP came up — firmware is healthy.
  // Mark valid BEFORE night-mode check, because handleNightMode() can
  // goToSleep() without returning and would otherwise let bootAttempts
  // climb unbounded across one night.
  markFirmwareValid();

  // OTA manifest check — placed BEFORE handleNightMode() so it can fire
  // during the night (first wake after midnight triggers it). Lets the
  // device update while the user is asleep; new firmware has ~7 hours
  // to soak before the dashboard wakes for the morning.
  checkForUpdates();

  handleNightMode();
  DBGLN("Night mode check passed");

  // Fetch the forecast (hourly + daily) from Open-Meteo. Current weather
  // and 2h rain nowcast come from Buienradar — see fetchBuienradar* below.
  DBGLN("Fetching Open-Meteo (forecast only)...");
  float temp = 0, wind = 0;
  WeatherCategory currentCategory = OVERCAST;
  WeatherExtras extras = {};
  DayForecast weekForecast[7];
  int forecastCount = 0;
  float rainData[24];
  char timeLabels[24][6];
  int rainCount = 0;
  bool forecastOk = fetchOpenMeteo(extras, weekForecast, forecastCount);
  DBG("Forecast: "); DBGLN(forecastOk ? "OK" : "FAIL");

  // Buienradar — current conditions. Live KNMI station observations.
  DBGLN("Fetching Buienradar (now)...");
  bool nowFetched = fetchBuienradarNow(temp, wind, currentCategory, extras.windDirection);
  bool weatherOk = false;
  if (nowFetched) {
    brCache.nowValid    = true;
    brCache.temp        = temp;
    brCache.category    = currentCategory;
    brCache.wind        = wind;
    brCache.windBearing = extras.windDirection;
    weatherOk = true;
  } else if (brCache.nowValid) {
    temp                  = brCache.temp;
    currentCategory       = brCache.category;
    wind                  = brCache.wind;
    extras.windDirection  = brCache.windBearing;
    weatherOk = true;
    DBGLN("BR now: using RTC cache");
  }

  // Buienradar — 2h rain nowcast.
  DBGLN("Fetching Buienradar (rain)...");
  bool rainFetched = fetchBuienradarRain(rainData, timeLabels, rainCount);
  bool rainOk = false;
  if (rainFetched) {
    brCache.rainValid = true;
    brCache.rainCount = rainCount;
    for (int i = 0; i < rainCount && i < 24; i++) {
      brCache.rainMmh[i] = rainData[i];
      strlcpy(brCache.rainLabels[i], timeLabels[i], sizeof(brCache.rainLabels[i]));
    }
    rainOk = true;
  } else if (brCache.rainValid) {
    rainCount = brCache.rainCount;
    for (int i = 0; i < rainCount && i < 24; i++) {
      rainData[i] = brCache.rainMmh[i];
      strlcpy(timeLabels[i], brCache.rainLabels[i], sizeof(timeLabels[i]));
    }
    rainOk = true;
    DBGLN("BR rain: using RTC cache");
  }

  if (weatherOk) {
    DBG("Now: temp="); DBG(temp);
    DBG(" wind="); DBG(wind);
    DBG(" cat="); DBGLN((int)currentCategory);
  }

  // Fetch train data from Central
  // Fetch trips from both Centraal and HS to Tilburg Universiteit, then
  // run the per-slot picker (A_Calculations.ino) to substitute HS trips
  // when a Centraal slot is disrupted.
  DBGLN("Fetching trips GVC -> TBU...");
  Departure ctrRaw[6];
  int nCtr = fetchTrips(STATION_CODE_CENTRAL, STATION_CODE_DESTINATION, ORIGIN_CTR, ctrRaw, 6);
  DBG("CTR raw: "); DBGLN(nCtr);
  nCtr = filterDominatedTrips(ctrRaw, nCtr);
  DBG("CTR after dominance filter: "); DBGLN(nCtr);

  // Conditional GV fetch — see docs/tier1-implementation-plans.md §3.
  // Clean CTR → picker won't consult HS → serve cache (or refresh if stale).
  // Disrupted CTR → always fetch fresh GV; only fall back to cache if that
  // fetch also fails, which is strictly better than today's nHs=0 behavior.
  Departure hsRaw[6];
  int nHs = 0;
  bool ctrDisrupted = ctrHasDisruption(ctrRaw, nCtr);
  bool cacheFresh   = (hsCache.magic == HS_CACHE_MAGIC) &&
                      (lastNtpSync != 0) &&
                      ((time(nullptr) - hsCache.fetchedAt) < (long)HS_CACHE_TTL_MIN * 60);

  if (!ctrDisrupted && cacheFresh) {
    nHs = hsCache.count;
    for (int i = 0; i < nHs && i < 6; i++) hsRaw[i] = hsCache.trips[i];
    long ageMin = (time(nullptr) - hsCache.fetchedAt) / 60;
    DBG("HS  raw: "); DBG(nHs);
    DBG(" (cached, "); DBG(ageMin); DBGLN("m old)");
  } else {
    DBG("Fetching trips GV -> TBU...");
    if (ctrDisrupted) DBGLN(" [CTR disrupted, forcing fresh fetch]");
    else              DBGLN(" [cache stale/empty]");
    nHs = fetchTrips(STATION_CODE_HS, STATION_CODE_DESTINATION, ORIGIN_HS, hsRaw, 6);
    DBG("HS  raw: "); DBGLN(nHs);
    nHs = filterDominatedTrips(hsRaw, nHs);

    if (nHs > 0) {
      // Cache the post-filter list — smaller, matches what the picker sees.
      hsCache.magic     = HS_CACHE_MAGIC;
      hsCache.fetchedAt = time(nullptr);
      hsCache.count     = nHs;
      for (int i = 0; i < nHs && i < 6; i++) hsCache.trips[i] = hsRaw[i];
    } else if (ctrDisrupted && hsCache.magic == HS_CACHE_MAGIC && hsCache.count > 0) {
      // Disrupted-path fetch failed — fall back to cache so the picker
      // still has substitution candidates. Strictly better than today.
      nHs = hsCache.count;
      for (int i = 0; i < nHs && i < 6; i++) hsRaw[i] = hsCache.trips[i];
      long ageMin = (time(nullptr) - hsCache.fetchedAt) / 60;
      DBG("HS  fetch failed, using cache ("); DBG(ageMin); DBGLN("m old)");
    }
  }

  DBG("HS  after dominance filter: "); DBGLN(nHs);

  Departure departures[3];
  int departureCount = pickDepartures(ctrRaw, nCtr, hsRaw, nHs, departures);
  DBG("Picked slots: "); DBGLN(departureCount);
#if DEBUG_LOG
  for (int i = 0; i < departureCount; i++) {
    DBG("  slot "); DBG(i);
    DBG(": "); DBG(departures[i].origin == ORIGIN_HS ? "HS  " : "CTR ");
    DBG(departures[i].time);
    DBG(" trk="); DBG(departures[i].track);
    DBG(" cancelled="); DBG(departures[i].cancelled ? "Y" : "N");
    DBG(" delay="); DBG(departures[i].delay[0] ? departures[i].delay : "-");
    DBG(" note="); DBGLN(departures[i].note[0] ? departures[i].note : "-");
  }
#endif

  // Network done — power down WiFi before the slow display refresh.
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);

  // Drop CPU clock for the display refresh phase. e-ink SPI is bit-banged
  // slowly and doesn't benefit from 240 MHz; this trims a few mA*sec per wake.
  setCpuFrequencyMhz(80);

  DBGLN("Calling updateDisplay...");
  updateDisplay(
    temp, wind, currentCategory, weatherOk, extras,
    rainData, timeLabels, rainCount, rainOk,
    weekForecast, forecastCount, forecastOk,
    departures, departureCount
  );
  DBGLN("Display updated!");

  wakeCounter++;
  DBGLN("Going to sleep...");
  goToSleep(SLEEP_DURATION);
}

void loop() {
  // Empty - deep sleep handles the "looping" by restarting setup()
}
