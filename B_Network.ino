// ============================================================================
// NETWORK: Wi-Fi and the one request to the screen service
// ============================================================================

// Connect, with the optional static IP from config.h. Returns false when no
// usable network came up within ~20 s (twice that with the DHCP retry).
bool connectWifi() {
  WiFi.mode(WIFI_STA);

#ifdef WIFI_STATIC_IP
  IPAddress ip, gw, sn, dns;
  ip.fromString(WIFI_STATIC_IP);
  gw.fromString(WIFI_GATEWAY);
  sn.fromString(WIFI_SUBNET);
  dns.fromString(WIFI_DNS);
  WiFi.config(ip, gw, sn, dns);
#endif

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);

#ifdef WIFI_STATIC_IP
  // Fall back to DHCP if the static address gives no working network.
  // Associated-but-wrong-subnet (a router swap) still reports WL_CONNECTED,
  // because a static stack never asks anyone for an address; a DNS lookup
  // has to cross the gateway, which is exactly what a wrong subnet breaks.
  // Without this, a stale static IP leaves a device that boots fine and can
  // never reach the network, so neither rollback nor OTA can help it.
  bool usable = (WiFi.status() == WL_CONNECTED);
  if (usable) {
    IPAddress probe;
    if (WiFi.hostByName("pool.ntp.org", probe) != 1) {
      DBGLN("Wi-Fi: associated but DNS probe failed, static IP likely stale");
      usable = false;
    }
  }
  if (!usable) {
    DBGLN("Wi-Fi: static IP failed, retrying with DHCP");
    WiFi.disconnect(true);
    WiFi.config(IPAddress(0, 0, 0, 0), IPAddress(0, 0, 0, 0), IPAddress(0, 0, 0, 0));
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
  }
#endif

  bool ok = (WiFi.status() == WL_CONNECTED);
  DBG("Wi-Fi: "); DBGLN(ok ? WiFi.localIP().toString() : String("failed"));
  return ok;
}

// X-Sleep, clamped; SLEEP_DEFAULT_S when missing or not a number.
static uint32_t parseSleep(const String& s) {
  if (s.length() == 0) return SLEEP_DEFAULT_S;
  long v = s.toInt();
  if (v <= 0) return SLEEP_DEFAULT_S;
  if (v < SLEEP_MIN_S) return SLEEP_MIN_S;
  if (v > SLEEP_MAX_S) return SLEEP_MAX_S;
  return (uint32_t)v;
}

// GET /v1/screen. The query string is this wake's report. On a 200 the frame
// lands in `frame`; anything but exactly FRAME_BYTES counts as a failure, so
// a cut-off download can never reach the panel.
ScreenReply fetchScreen(uint8_t* frame, float batteryV) {
  ScreenReply r = { false, false, true, false, SLEEP_DEFAULT_S };

  char url[256];
  snprintf(url, sizeof(url),
           "%s?batt=%.2f&fw=%s&rssi=%d&wake=%lu&fail=%u&awake_ms=%lu&wifi_ms=%lu",
           SCREEN_URL, batteryV, FIRMWARE_VERSION, (int)WiFi.RSSI(),
           (unsigned long)wakeCounter, (unsigned)failStreak,
           (unsigned long)prevAwakeMs, (unsigned long)prevWifiMs);

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(2000);
  http.setTimeout(5000);
  const char* headerKeys[] = { "X-Sleep", "X-Refresh", "X-Ota" };
  http.collectHeaders(headerKeys, 3);

  if (!http.begin(client, url)) {
    DBGLN("Screen: http.begin failed");
    return r;
  }
  uint32_t start = millis();
  int code = http.GET();
  DBG("Screen: HTTP "); DBG(code); DBG(" in "); DBG(millis() - start); DBGLN(" ms");

  if (code == 200 || code == 204) {
    r.sleepS      = parseSleep(http.header("X-Sleep"));
    r.otaHint     = (http.header("X-Ota") == "1");
    r.fullRefresh = (http.header("X-Refresh") != "partial");
  }

  if (code == 204) {
    r.ok = true;           // keep the panel as it is
  } else if (code == 200 && http.getSize() == FRAME_BYTES) {
    WiFiClient* stream = http.getStreamPtr();
    size_t got = 0;
    uint32_t deadline = millis() + 5000;
    while (got < FRAME_BYTES && millis() < deadline &&
           (http.connected() || stream->available())) {
      size_t avail = stream->available();
      if (avail) {
        got += stream->readBytes(frame + got, min(avail, (size_t)(FRAME_BYTES - got)));
      } else {
        delay(1);
      }
    }
    DBG("Screen: frame "); DBG(got); DBGLN(" bytes");
    r.ok = r.hasFrame = (got == FRAME_BYTES);
  }
  http.end();
  return r;
}
