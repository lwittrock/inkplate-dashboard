// ============================================================================
// OTA (Over-The-Air firmware updates) — Phase 3: real updates + rollback
// ============================================================================
// checkForUpdates() fetches the manifest, compares versions, and on a
// newer remote calls performUpdate() which actually flashes the new
// firmware and reboots. App-level rollback (NOT bootloader rollback —
// stock Arduino-ESP32 doesn't enable that): if a new firmware fails to
// reach markFirmwareValid() within OTA_BOOT_FAILURE_LIMIT boots, we
// switch back to the previous partition.
//
// State lives in RTC RAM, protected by a magic-word sentinel because RTC
// slow memory contents are undefined on cold boot (battery removal).
// The design is in CLAUDE.md ("OTA gotchas", "OTA design decisions").
//
// When to check (thin client, docs/server-rendering-design.md): the device
// has no clock any more, so the old "first wake after midnight" gate is gone.
//   - the server's X-Ota hint, sent on one wake just after midnight;
//   - the device's own trigger: 48 hours of summed sleep without a check;
//   - while the server is unreachable: on the second failed wake, then every
//     12 hours of sleep. OTA must never depend on the server, or a release
//     that broke the server request could never be replaced over the air.

#include <HTTPUpdate.h>
#include <esp_ota_ops.h>

#ifndef OTA_MANIFEST_URL
#define OTA_MANIFEST_URL \
  "https://raw.githubusercontent.com/lwittrock/inkplate-dashboard/firmware-latest/version.txt"
#endif

// Sentinel proving the RTC state was initialized by this firmware lineage,
// not garbage left over from a power loss. Bump if the RTC schema changes.
// 0xC0FFEE42 until the thin client replaced otaLastCheckDay with otaSleptS.
// (0xC0FFEE44 to 46 were the old firmware's cache sentinels: not reused.)
#define OTA_RTC_MAGIC 0xC0FFEE47UL

// Seconds of sleep between the device's own checks, and while failing.
#define OTA_CHECK_EVERY_S         (48UL * 3600)
#define OTA_CHECK_WHILE_FAILING_S (12UL * 3600)

// Trigger rollback if a new firmware fails to reach markFirmwareValid()
// this many boots in a row. 3 = one transient failure tolerated.
#define OTA_BOOT_FAILURE_LIMIT 3

RTC_DATA_ATTR uint32_t otaRtcMagic         = 0;
RTC_DATA_ATTR uint32_t otaSleptS           = 0;       // sleep since the last check
RTC_DATA_ATTR char     otaPendingVersion[24]    = {0};
RTC_DATA_ATTR char     otaLastFailedVersion[24] = {0};
RTC_DATA_ATTR uint8_t  otaBootAttempts     = 0;

// Reset RTC state on cold boot. Safe to call every wake — the magic check
// is the cheap path; the body only runs when slow memory is uninitialized.
void initOtaState() {
  if (otaRtcMagic != OTA_RTC_MAGIC) {
    DBGLN("OTA: cold-boot detected, resetting RTC state");
    otaRtcMagic              = OTA_RTC_MAGIC;
    otaSleptS                = OTA_CHECK_EVERY_S;  // so a cold boot checks once
    otaPendingVersion[0]     = '\0';
    otaLastFailedVersion[0]  = '\0';
    otaBootAttempts          = 0;
  }
}

// Called at the very top of setup() after initOtaState(). If a pending OTA
// has failed to reach markFirmwareValid() too many times, flip the boot
// partition and restart — the previous firmware lives in the other slot.
void checkBootAttempts() {
  if (otaBootAttempts >= OTA_BOOT_FAILURE_LIMIT && otaPendingVersion[0] != '\0') {
    DBG("OTA: rollback — ");
    DBG(otaBootAttempts);
    DBG(" failed boots of ");
    DBGLN(otaPendingVersion);

    strlcpy(otaLastFailedVersion, otaPendingVersion, sizeof(otaLastFailedVersion));
    otaPendingVersion[0] = '\0';
    otaBootAttempts      = 0;

    const esp_partition_t* other = esp_ota_get_next_update_partition(NULL);
    if (other) {
      esp_ota_set_boot_partition(other);
    } else {
      DBGLN("OTA: rollback failed — no other partition available");
    }
    esp_restart();  // does not return
  }
  otaBootAttempts++;
}

// Reset the boot-attempts counter and clear pending OTA marker. Called as
// soon as Wi-Fi is up, before the server request: if Wi-Fi came up, the
// firmware is healthy enough to commit to, and a server outage must not
// count against it. A release that breaks only the server request is
// caught by the OTA triggers while failing instead.
void markFirmwareValid() {
  if (otaPendingVersion[0] != '\0') {
    DBG("OTA: marked valid after update to ");
    DBGLN(otaPendingVersion);
  }
  otaBootAttempts      = 0;
  otaPendingVersion[0] = '\0';
}

// Download and flash the new firmware. On success the device reboots into
// the new slot automatically. On failure: log + record the version so we
// don't retry it.
static void performUpdate(const char* version, const char* url) {
  // Stage pending BEFORE the update. If we crash mid-download, the new
  // firmware's checkBootAttempts() uses this to detect "OTA in flight."
  strlcpy(otaPendingVersion, version, sizeof(otaPendingVersion));

  WiFiClientSecure client;
  client.setInsecure();

  httpUpdate.setLedPin(-1);
  httpUpdate.rebootOnUpdate(true);
  httpUpdate.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);

  DBG("OTA: downloading from "); DBGLN(url);
  t_httpUpdate_return result = httpUpdate.update(client, url);

  // Only reached on failure — success reboots into the new slot.
  DBG("OTA: update failed, result="); DBG((int)result);
  DBG(" err="); DBG(httpUpdate.getLastError());
  DBG(" msg="); DBGLN(httpUpdate.getLastErrorString());

  strlcpy(otaLastFailedVersion, otaPendingVersion, sizeof(otaLastFailedVersion));
  otaPendingVersion[0] = '\0';
}

// The device's own trigger, on a wake that reached the server.
bool otaOwnTriggerDue() {
#ifdef OTA_TEST_FORCE_CHECK
  DBGLN("OTA: TEST mode, forcing check");
  return true;
#endif
  return otaSleptS >= OTA_CHECK_EVERY_S;
}

// On a wake where Wi-Fi works but the server does not.
bool otaFailureTriggerDue(uint8_t failStreak) {
  return failStreak == FAILS_BEFORE_MESSAGE || otaSleptS >= OTA_CHECK_WHILE_FAILING_S;
}

// Called by goToSleep() with each sleep's length.
void otaNoteSleep(uint32_t seconds) {
  otaSleptS = (otaSleptS > UINT32_MAX - seconds) ? UINT32_MAX : otaSleptS + seconds;
}

// Fetch version.txt from GitHub. Two-line format: version, then binary URL.
// Returns false on any failure; outVersion/outUrl are unchanged in that case.
static bool fetchManifest(char* outVersion, size_t versionSize,
                          char* outUrl, size_t urlSize) {
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);
  http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);

  if (!http.begin(client, OTA_MANIFEST_URL)) {
    DBGLN("OTA: http.begin failed");
    return false;
  }

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    DBG("OTA: manifest HTTP "); DBGLN(code);
    http.end();
    return false;
  }

  // Slurp-then-parse, same pattern as the rest of the codebase.
  String body = http.getString();
  http.end();

  int nl = body.indexOf('\n');
  if (nl <= 0) {
    DBGLN("OTA: manifest malformed (no newline)");
    return false;
  }

  String ver    = body.substring(0, nl);    ver.trim();
  String binUrl = body.substring(nl + 1);   binUrl.trim();

  if (ver.length() == 0 || ver.length() >= versionSize) {
    DBGLN("OTA: version field bad length");
    return false;
  }
  if (binUrl.length() == 0 || binUrl.length() >= urlSize) {
    DBGLN("OTA: URL field bad length");
    return false;
  }

  strlcpy(outVersion, ver.c_str(),    versionSize);
  strlcpy(outUrl,     binUrl.c_str(), urlSize);
  return true;
}

// Fetch the manifest and update if it names a newer release. Called only
// when a trigger above says so, with Wi-Fi up. The counter restarts even if
// the manifest can't be fetched: the next trigger retries, not every wake.
void checkForUpdates() {
#ifdef OTA_SKIP
  // Bench builds only (config.h.example). CI refuses a CONFIG_H with it.
  DBGLN("OTA: skipped, OTA_SKIP is set");
  return;
#endif
  otaSleptS = 0;
  DBGLN("OTA: checking for updates...");
  char remoteVersion[24];
  char remoteUrl[256];
  if (!fetchManifest(remoteVersion, sizeof(remoteVersion),
                     remoteUrl,     sizeof(remoteUrl))) {
    return;  // fetchManifest already logged the reason
  }

  DBG("OTA: current="); DBG(FIRMWARE_VERSION);
  DBG(" remote=");      DBGLN(remoteVersion);

  // Local "dev" builds (no CI-injected firmware_version.h) treat any
  // tagged remote as newer — otherwise lexical compare puts "dev" > all
  // digit-starting versions, blocking OTA from any local dev build.
  bool localIsDev = (strcmp(FIRMWARE_VERSION, "dev") == 0);

  if (!localIsDev && strcmp(remoteVersion, FIRMWARE_VERSION) == 0) {
    DBGLN("OTA: up to date");
    return;
  }

  if (otaLastFailedVersion[0] != '\0' &&
      strcmp(remoteVersion, otaLastFailedVersion) == 0) {
    DBGLN("OTA: skipping known-bad version");
    return;
  }

  if (!localIsDev && strcmp(remoteVersion, FIRMWARE_VERSION) <= 0) {
    DBGLN("OTA: remote is not newer, ignoring");
    return;
  }

  DBG("OTA: updating to "); DBGLN(remoteVersion);
  performUpdate(remoteVersion, remoteUrl);
  // If we reach here, update failed — performUpdate already logged.
}
