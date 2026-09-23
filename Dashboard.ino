// ============================================================================
// INKPLATE DASHBOARD — thin client
// ============================================================================
// The home server draws the screen (docs/server-rendering-design.md). Each
// wake: connect Wi-Fi, one plain-HTTP request to the screen service on the
// LAN, draw the 800x600 1-bit frame it returns, sleep for as long as it says.
// The request reports the battery, firmware and Wi-Fi signal on the way.
//
// Everything that used to live here (fetching, the train picker, the layout,
// night mode, the wake cadence) runs on the server now, so this firmware
// should rarely change. What stays is load-bearing: Wi-Fi, OTA with its
// rollback, and a readable message when the server can't be reached.

#include "Inkplate.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <esp_bt.h>
#include "esp32/rom/miniz.h"   // the ROM's inflater, for compressed frames
#include "config.h"

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
// Define DEBUG_LOG=1 in config.h to enable Serial output at 115200 baud.

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
// SETTINGS (all with defaults, so the CI config.h needs no new field)
// ============================================================================

// The screen service in CT 106. Override in config.h only if it moves.
#ifndef SCREEN_URL
#define SCREEN_URL "http://192.168.1.212:8088/v1/screen"
#endif

// Frame formats (docs/server-rendering-design.md, "The contract"), both
// zlib-compressed on the wire:
//   g4z  greyscale, the library's 3-bit buffer layout: 2 pixels per byte, 0-7
//   m1z  1-bit: 600 rows of 100 bytes, MSB first, 1 = black
#define GREY_BYTES     240000
#define MONO_BYTES     60000
#define COMPRESSED_MAX 131072   // a frame is ~7-15 KB; anything near this is wrong

// X-Sleep is clamped to this range; a missing or unreadable header gives the default.
#define SLEEP_MIN_S     300
#define SLEEP_MAX_S     28800
#define SLEEP_DEFAULT_S 1800

// Failure path: retry soon after one failure, then back off.
#define RETRY_FIRST_S   600     // after the first failure: nothing drawn yet
#define RETRY_SHOWN_S   1800    // after the message is up
#define RETRY_LONG_S    3600    // from the fourth failure
#define FAILS_BEFORE_MESSAGE 2  // one blip must not replace the dashboard

// ============================================================================
// STATE THAT SURVIVES DEEP SLEEP
// ============================================================================
// RTC RAM survives deep sleep but not a power loss or an OTA reboot (see
// CLAUDE.md), so every value here must be safe at its zero default.

enum FailKind : uint8_t { FAIL_NONE = 0, FAIL_WIFI = 1, FAIL_SERVER = 2 };

RTC_DATA_ATTR uint32_t wakeCounter   = 0;          // sent as `wake`; 0 = cold boot
RTC_DATA_ATTR uint8_t  failStreak    = 0;          // consecutive failed wakes
RTC_DATA_ATTR uint8_t  shownMessage  = FAIL_NONE;  // which message the panel shows, if any
RTC_DATA_ATTR uint32_t prevAwakeMs   = 0;          // last wake's active time, reported next wake
RTC_DATA_ATTR uint32_t prevWifiMs    = 0;          // last wake's Wi-Fi connect time

enum FrameFormat : uint8_t { FRAME_MONO = 0, FRAME_GREY = 1 };

// What the server's reply asked for.
struct ScreenReply {
  bool        ok;          // 200 with a whole frame, or 204
  bool        hasFrame;    // 200: draw it; 204: leave the panel as it is
  bool        fullRefresh; // 1-bit only; greyscale is always a full refresh
  bool        otaHint;
  uint32_t    sleepS;
  FrameFormat format;
};

// A 1-bit frame lands here; a greyscale one goes straight into the library's
// 3-bit buffer (display.DMemory4Bit). PSRAM: 60 KB don't fit in static RAM.
uint8_t* monoFrame = nullptr;

Inkplate display(INKPLATE_1BIT);

// ============================================================================
// MAIN
// ============================================================================

void setup() {
  uint32_t wakeStart = millis();

  // Bluetooth stays initialized by default on ESP32-Arduino and leaks power.
  btStop();
  esp_bt_controller_disable();

#if DEBUG_LOG
  Serial.begin(115200);
  delay(200);
#endif
  DBGLN("\n=== DASHBOARD thin client ===");
  DBG("FW: "); DBG(FIRMWARE_VERSION); DBG("  wake #"); DBGLN(wakeCounter);

  display.begin();
  initOtaState();      // reset OTA RTC state on cold boot (sentinel mismatch)
  checkBootAttempts(); // may roll back and restart if a new firmware keeps failing

  // Read before the radio is on: Wi-Fi's current draw pulls the reading down.
  float batteryV = display.readBattery();

  uint32_t wifiStart = millis();
  bool wifiOk = connectWifi();
  prevWifiMs = millis() - wifiStart;

  if (!wifiOk) {
    failedWake(FAIL_WIFI, wakeStart);   // does not return
  }

  // Wi-Fi came up: this firmware is healthy enough to keep. A server outage
  // must not count against it, so this comes before the server request.
  markFirmwareValid();

  // Never freed: deep sleep resets everything.
  monoFrame = (uint8_t*)ps_malloc(MONO_BYTES);
  ScreenReply reply = fetchScreen(batteryV);

  if (!reply.ok) {
    failedWake(FAIL_SERVER, wakeStart); // does not return
  }

  failStreak = 0;
  if (reply.otaHint || otaOwnTriggerDue()) {
    checkForUpdates();                  // reboots into new firmware if there is one
  }

  // Network done; the panel refresh is slow and needs no radio.
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  setCpuFrequencyMhz(80);

  if (reply.hasFrame) {
    if (reply.format == FRAME_GREY) drawGrey();
    else drawFrame(monoFrame, reply.fullRefresh);
    shownMessage = FAIL_NONE;
  }

  wakeCounter++;
  prevAwakeMs = millis() - wakeStart;
  goToSleep(reply.sleepS);
}

void loop() {
  // Empty: deep sleep restarts setup() on every wake.
}

// ============================================================================
// FAILURE PATH
// ============================================================================

// A wake that could not reach Wi-Fi or the server. The first failure leaves
// the panel alone (e-ink keeps its picture without power) and retries soon;
// from the second, the panel says which part failed. The message is drawn
// once, not on every failed wake after it.
void failedWake(FailKind kind, uint32_t wakeStart) {
  if (failStreak < 255) failStreak++;
  DBG("Wake failed: "); DBG(kind == FAIL_WIFI ? "no Wi-Fi" : "server");
  DBG(", streak "); DBGLN(failStreak);

  // OTA must never depend on the server: a release that broke the request
  // could otherwise never be replaced over the air. Needs Wi-Fi, though.
  if (kind == FAIL_SERVER && otaFailureTriggerDue(failStreak)) {
    checkForUpdates();
  }

  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  setCpuFrequencyMhz(80);

  if (failStreak >= FAILS_BEFORE_MESSAGE && shownMessage != kind) {
    drawMessage(kind == FAIL_WIFI ? "No Wi-Fi" : "Server down");
    shownMessage = kind;
  }

  uint32_t retry = failStreak < FAILS_BEFORE_MESSAGE ? RETRY_FIRST_S
                 : failStreak < 4                    ? RETRY_SHOWN_S
                                                     : RETRY_LONG_S;
  wakeCounter++;
  prevAwakeMs = millis() - wakeStart;
  goToSleep(retry);
}

void goToSleep(uint32_t seconds) {
#ifdef BENCH_MAX_SLEEP_S
  // Bench builds only (config.h.example): short sleeps so a test session
  // doesn't wait half an hour per wake. CI refuses a CONFIG_H with it.
  if (seconds > BENCH_MAX_SLEEP_S) seconds = BENCH_MAX_SLEEP_S;
#endif
  DBG("Deep sleep: "); DBG(seconds); DBGLN(" s");
  otaNoteSleep(seconds);
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  esp_deep_sleep_start();
}
