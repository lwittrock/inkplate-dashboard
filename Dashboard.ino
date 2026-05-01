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

// Train departure information
struct Train {
  char time[6];   // "HH:MM"
  char track[6];  // e.g. "12a"
  char delay[8];  // e.g. "+12m"
  bool cancelled;
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
  DBGLN("\n\n=== DASHBOARD STARTING ===");
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
  DayForecast weekForecast[7];
  int forecastCount = 0;
  float rainData[12];
  char timeLabels[12][20];
  int rainCount = 0;
  bool rainOk = false;
  bool weatherOk = fetchOpenMeteo(
    temp, wind, weatherCode,
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
  DBGLN("Fetching trains from Central...");
  Train trainsCentral[3];
  int trainCountCentral = getTrains(trainsCentral, 3, STATION_CODE_CENTRAL);
  DBG("Trains from Central: "); DBGLN(trainCountCentral);

  // Check if all Central trains are cancelled
  bool allCentralCancelled = true;
  if (trainCountCentral > 0) {
    for (int i = 0; i < trainCountCentral; i++) {
      if (!trainsCentral[i].cancelled) {
        allCentralCancelled = false;
        break;
      }
    }
  }

  // Fetch HS trains if Central has no trains OR all are cancelled
  Train trainsHS[3];
  int trainCountHS = 0;
  if (trainCountCentral == 0 || allCentralCancelled) {
    DBGLN("Fetching trains from HS...");
    trainCountHS = getTrains(trainsHS, 3, STATION_CODE_HS);
    DBG("Trains from HS: "); DBGLN(trainCountHS);
  }

  // Network done — power down WiFi before the slow display refresh.
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);

  // Drop CPU clock for the display refresh phase. e-ink SPI is bit-banged
  // slowly and doesn't benefit from 240 MHz; this trims a few mA*sec per wake.
  setCpuFrequencyMhz(80);

  DBGLN("Calling updateDisplay...");
  updateDisplay(
    temp, wind, weatherCode, weatherOk,
    rainData, timeLabels, rainCount, rainOk,
    weekForecast, forecastCount, weatherOk,
    trainsCentral, trainCountCentral,
    trainsHS, trainCountHS
  );
  DBGLN("Display updated!");

  wakeCounter++;
  DBGLN("Going to sleep...");
  goToSleep(SLEEP_DURATION);
}

void loop() {
  // Empty - deep sleep handles the "looping" by restarting setup()
}
