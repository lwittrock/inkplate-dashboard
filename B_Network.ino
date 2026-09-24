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

// Read exactly `len` bytes of body into `dst`, within 5 s. Returns what arrived.
static size_t readBody(HTTPClient& http, uint8_t* dst, size_t len) {
  WiFiClient* stream = http.getStreamPtr();
  size_t got = 0;
  uint32_t deadline = millis() + 5000;
  while (got < len && millis() < deadline && (http.connected() || stream->available())) {
    size_t avail = stream->available();
    if (avail) {
      got += stream->readBytes(dst + got, min(avail, len - got));
    } else {
      delay(1);
    }
  }
  return got;
}

// Decompress a zlib stream (header and Adler-32 checked) with the ESP32's ROM
// inflater into exactly `outLen` bytes. The decompressor state (~11 KB) goes
// on the heap: setup() runs on an 8 KB stack.
static bool inflateExact(const uint8_t* in, size_t inLen, uint8_t* out, size_t outLen) {
  tinfl_decompressor* d = (tinfl_decompressor*)malloc(sizeof(tinfl_decompressor));
  if (!d) return false;
  tinfl_init(d);
  size_t inBytes = inLen, outBytes = outLen;
  tinfl_status st = tinfl_decompress(d, in, &inBytes, out, out, &outBytes,
                                     TINFL_FLAG_PARSE_ZLIB_HEADER |
                                     TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF |
                                     TINFL_FLAG_COMPUTE_ADLER32);
  free(d);
  DBG("Screen: inflate status "); DBG((int)st); DBG(", "); DBG(outBytes); DBGLN(" bytes");
  return st == TINFL_STATUS_DONE && outBytes == outLen;
}

// GET /v1/screen. The query string is this wake's report and the formats this
// firmware can draw; X-Format says which one came back. A greyscale frame is
// decompressed straight into the library's 3-bit buffer; a 1-bit one into
// `monoFrame`. Anything that doesn't decompress to exactly the format's size
// is a failure, so a damaged download can never reach the panel.
ScreenReply fetchScreen(float batteryV) {
  ScreenReply r = { false, false, true, false, SLEEP_DEFAULT_S, FRAME_MONO };

  char url[288];
  snprintf(url, sizeof(url),
           "%s?fmt=g4z,m1z&batt=%.2f&fw=%s&rssi=%d&wake=%lu&fail=%u&awake_ms=%lu&wifi_ms=%lu",
           SCREEN_URL, batteryV, FIRMWARE_VERSION, (int)WiFi.RSSI(),
           (unsigned long)wakeCounter, (unsigned)failStreak,
           (unsigned long)prevAwakeMs, (unsigned long)prevWifiMs);

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(2000);
  http.setTimeout(5000);
  const char* headerKeys[] = { "X-Sleep", "X-Refresh", "X-Ota", "X-Format" };
  http.collectHeaders(headerKeys, 4);

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

  int size = http.getSize();
  String fmt = http.header("X-Format");
  if (code == 204) {
    r.ok = true;           // keep the panel as it is
  } else if (code == 200 && size > 0 && size <= COMPRESSED_MAX && (fmt == "g4z" || fmt == "m1z")) {
    uint8_t* body = (uint8_t*)ps_malloc(size);
    if (body) {
      size_t got = readBody(http, body, size);
      DBG("Screen: "); DBG(fmt); DBG(" "); DBG(got); DBGLN(" bytes");
      if (got == (size_t)size) {
        if (fmt == "g4z") {
          r.format = FRAME_GREY;
          r.ok = inflateExact(body, got, display.DMemory4Bit, GREY_BYTES);
        } else {
          r.format = FRAME_MONO;
          r.ok = monoFrame && inflateExact(body, got, monoFrame, MONO_BYTES);
        }
        r.hasFrame = r.ok;
      }
      free(body);
    }
  }
  http.end();
  return r;
}
