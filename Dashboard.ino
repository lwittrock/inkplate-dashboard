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
  char note[32];                 // set by the picker when an HS trip replaces a CTR slot
  char plannedDepartureISO[26];  // "2026-05-24T12:19:00+0200" — for cross-origin sort
};

// Extra current-weather signals not in the legacy fetchOpenMeteo signature.
// Populated alongside the existing temp/wind/code outputs by the extended
// Open-Meteo query (current=…,wind_direction_10m; hourly=temperature_2m).
struct WeatherExtras {
  int   windDirection;    // degrees from N, 0-360
  float hourlyTemp[24];   // next 24h starting from current hour
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
  DBG("Wake #"); DBGLN(wakeCounter);

  display.begin();
  DBGLN("Display initialized");

  initHardware();
  DBGLN("Hardware initialized");

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

  DBGLN("Fetching trips GV -> TBU...");
  Departure hsRaw[6];
  int nHs = fetchTrips(STATION_CODE_HS, STATION_CODE_DESTINATION, ORIGIN_HS, hsRaw, 6);
  DBG("HS  raw: "); DBGLN(nHs);
  nHs = filterDominatedTrips(hsRaw, nHs);
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
