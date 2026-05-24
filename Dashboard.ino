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

// Force a full refresh every Nth wake to clear ghosting from partial updates.
// With SLEEP_DURATION=900 (15 min), 4 = once per hour.
#ifndef FULL_REFRESH_EVERY
#define FULL_REFRESH_EVERY 4
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

  // Fetch weather, forecast, and rain in a single Open-Meteo call
  DBGLN("Fetching Open-Meteo...");
  float temp = 0, wind = 0;
  int weatherCode = 0;
  WeatherExtras extras = {};
  DayForecast weekForecast[7];
  int forecastCount = 0;
  float rainData[12];
  char timeLabels[12][20];
  int rainCount = 0;
  bool rainOk = false;
  bool weatherOk = fetchOpenMeteo(
    temp, wind, weatherCode, extras,
    weekForecast, forecastCount,
    rainData, timeLabels, rainCount, rainOk);
  DBG("Weather: "); DBG(weatherOk ? "OK" : "FAIL");
  DBG(", Rain: "); DBGLN(rainOk ? "OK" : "FAIL");
  if (weatherOk) {
    DBG("Temp="); DBG(temp);
    DBG(" Wind="); DBG(wind);
    DBG(" Code="); DBGLN(weatherCode);
  }

  // Fetch train data from Central
  // Fetch trips from both Centraal and HS to Tilburg Universiteit, then
  // run the per-slot picker (A_Calculations.ino) to substitute HS trips
  // when a Centraal slot is disrupted.
  DBGLN("Fetching trips GVC -> TBU...");
  Departure ctrRaw[6];
  int nCtr = fetchTrips(STATION_CODE_CENTRAL, STATION_CODE_DESTINATION, ORIGIN_CTR, ctrRaw, 6);
  DBG("CTR raw: "); DBGLN(nCtr);

  DBGLN("Fetching trips GV -> TBU...");
  Departure hsRaw[6];
  int nHs = fetchTrips(STATION_CODE_HS, STATION_CODE_DESTINATION, ORIGIN_HS, hsRaw, 6);
  DBG("HS  raw: "); DBGLN(nHs);

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
    temp, wind, weatherCode, weatherOk, extras,
    rainData, timeLabels, rainCount, rainOk,
    weekForecast, forecastCount, weatherOk,
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
