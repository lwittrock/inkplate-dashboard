// ============================================================================
// OTA (Over-The-Air firmware updates)
// ============================================================================
// Phase 2: log-only. checkForUpdates() fetches the manifest from GitHub and
// compares it to FIRMWARE_VERSION, logging what it would do. No real update
// happens yet — that's Phase 3.
//
// State lives in RTC RAM, protected by a magic-word sentinel because RTC
// slow memory contents are undefined on cold boot (battery removal).
// See docs/ota-updates-plan.md for the full design.

#ifndef OTA_MANIFEST_URL
#define OTA_MANIFEST_URL \
  "https://raw.githubusercontent.com/lwittrock/inkplate-dashboard/firmware-latest/version.txt"
#endif

// Sentinel proving the RTC state was initialized by this firmware lineage,
// not garbage left over from a power loss. Bump if the RTC schema changes.
#define OTA_RTC_MAGIC 0xC0FFEE42UL

RTC_DATA_ATTR uint32_t otaRtcMagic         = 0;
RTC_DATA_ATTR uint16_t otaLastCheckDay     = 0xFFFF;  // 0xFFFF = "never"
RTC_DATA_ATTR char     otaPendingVersion[24]    = {0};
RTC_DATA_ATTR char     otaLastFailedVersion[24] = {0};
RTC_DATA_ATTR uint8_t  otaBootAttempts     = 0;

// Reset RTC state on cold boot. Safe to call every wake — the magic check
// is the cheap path; the body only runs when slow memory is uninitialized.
void initOtaState() {
  if (otaRtcMagic != OTA_RTC_MAGIC) {
    DBGLN("OTA: cold-boot detected, resetting RTC state");
    otaRtcMagic              = OTA_RTC_MAGIC;
    otaLastCheckDay          = 0xFFFF;
    otaPendingVersion[0]     = '\0';
    otaLastFailedVersion[0]  = '\0';
    otaBootAttempts          = 0;
  }
}

// Returns true if we should fetch the manifest this wake.
// Gated by: NTP synced, hour >= NIGHT_END, lastCheckDay != today.
// Bypassed entirely if OTA_TEST_FORCE_CHECK is defined.
static bool shouldCheckForUpdate() {
  time_t now = time(nullptr);
  if (now < 1700000000) {
    DBGLN("OTA: skip — NTP not synced");
    return false;
  }

#ifdef OTA_TEST_FORCE_CHECK
  DBGLN("OTA: TEST mode — forcing check");
  return true;
#endif

  struct tm tm_local;
  localtime_r(&now, &tm_local);
  if (tm_local.tm_hour < NIGHT_END) {
    return false;  // before morning, defer
  }

  uint16_t today = (uint16_t)tm_local.tm_yday;
  if (today == otaLastCheckDay) {
    return false;  // already checked today
  }
  otaLastCheckDay = today;
  return true;
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

// Top-level OTA check. Called once per setup() from Dashboard.ino, after
// the morning fetches so WiFi is already up. Phase 2: log-only.
void checkForUpdates() {
  if (!shouldCheckForUpdate()) return;

  DBGLN("OTA: checking for updates...");
  char remoteVersion[24];
  char remoteUrl[256];
  if (!fetchManifest(remoteVersion, sizeof(remoteVersion),
                     remoteUrl,     sizeof(remoteUrl))) {
    return;  // fetchManifest already logged the reason
  }

  DBG("OTA: current="); DBG(FIRMWARE_VERSION);
  DBG(" remote=");      DBGLN(remoteVersion);

  if (strcmp(remoteVersion, FIRMWARE_VERSION) == 0) {
    DBGLN("OTA: up to date");
    return;
  }

  if (otaLastFailedVersion[0] != '\0' &&
      strcmp(remoteVersion, otaLastFailedVersion) == 0) {
    DBGLN("OTA: skipping known-bad version");
    return;
  }

  // Lexical compare on zero-padded YYYY.MM.DD-NN sorts correctly.
  if (strcmp(remoteVersion, FIRMWARE_VERSION) <= 0) {
    DBGLN("OTA: remote is not newer, ignoring");
    return;
  }

  DBG("OTA: WOULD UPDATE to "); DBGLN(remoteVersion);
  DBG("OTA: from ");             DBGLN(remoteUrl);
  // Phase 3 will call performUpdate(remoteVersion, remoteUrl) here.
}
