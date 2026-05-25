// ============================================================================
// NETWORK FUNCTIONS
// ============================================================================
// WiFi, NTP time sync, and API data fetching

// ============================================================================
// NETWORK INFRASTRUCTURE
// ============================================================================

void initHardware() {
  WiFi.mode(WIFI_STA);

  // Optional static IP — skips DHCP (~1–2 s faster connect, less radio time).
  // Define WIFI_STATIC_IP / GATEWAY / SUBNET / DNS in config.h to use it.
#ifdef WIFI_STATIC_IP
  IPAddress ip, gw, sn, dns;
  ip.fromString(WIFI_STATIC_IP);
  gw.fromString(WIFI_GATEWAY);
  sn.fromString(WIFI_SUBNET);
  dns.fromString(WIFI_DNS);
  WiFi.config(ip, gw, sn, dns);
#endif

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500);
    attempts++;
  }

  if (WiFi.status() != WL_CONNECTED) {
    DBGLN("WiFi failed");
    showError("WiFi Error");
    goToSleep(600);
  }

  // Use POSIX TZ string so the ESP32 handles DST automatically.
  // Note: configTime(0,0,...) would overwrite TZ with "GMT0"; configTzTime
  // sets the POSIX zone *after* SNTP setup, so DST is honored.
  configTzTime(TIMEZONE, "pool.ntp.org");
  struct tm timeinfo;
  attempts = 0;
  while (!getLocalTime(&timeinfo) && attempts < 30) {
    delay(500);
    attempts++;
  }
}

void handleNightMode() {
  struct tm timeinfo;
  if (getLocalTime(&timeinfo)) {
    int hour = timeinfo.tm_hour;
    if (hour >= NIGHT_START || hour < NIGHT_END) {
      int sleepHours = (hour >= NIGHT_START) ?
                       (24 - hour + NIGHT_END) : (NIGHT_END - hour);
      DBGLN("Entering Night Mode...");

      WiFi.disconnect(true);
      WiFi.mode(WIFI_OFF);
      esp_sleep_enable_timer_wakeup(sleepHours * 3600 * 1000000ULL);
      esp_deep_sleep_start();
    }
  }
}

void goToSleep(unsigned long seconds) {
  DBG("Deep sleep: "); DBG(seconds); DBGLN("s");
  // WiFi already disconnected by caller; harmless if called twice.
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  esp_sleep_enable_timer_wakeup(seconds * 1000000ULL);
  esp_deep_sleep_start();
}

// ============================================================================
// API DATA FETCHING
// ============================================================================

// Perform a GET with up to 3 attempts; returns HTTP status code or -1 on failure.
int httpGetWithRetry(HTTPClient &http, int maxRetries = 3) {
  for (int attempt = 1; attempt <= maxRetries; attempt++) {
    int code = http.GET();
    if (code == 200) return code;
    DBG("HTTP attempt "); DBG(attempt);
    DBG(" failed: "); DBGLN(code);
    if (attempt < maxRetries) delay(1000);
  }
  return -1;
}

// Open-Meteo call: 24h hourly temp curve + 7-day daily forecast only.
// Current conditions and 2-hour rain now come from Buienradar
// (fetchBuienradarNow / fetchBuienradarRain), which is observation-based
// and avoids OM's model-derived "phantom cloud" artefacts for NL.
bool fetchOpenMeteo(
    WeatherExtras &extras,
    DayForecast forecast[],
    int &forecastCount
  ) {
  extras.hourlyCount = 0;
  forecastCount = 0;

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);

  char url[512];
  snprintf(url, sizeof(url),
    "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
    "&hourly=temperature_2m&forecast_hours=24"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,"
    "sunshine_duration,daylight_duration,precipitation_sum,precipitation_hours,snowfall_sum,"
    "sunrise,sunset"
    "&forecast_days=7&timezone=auto",
    (double)LATITUDE, (double)LONGITUDE);

  if (!http.begin(client, url)) return false;

  int code = httpGetWithRetry(http);
  if (code != 200) {
    http.end();
    return false;
  }

  // Slurp into a String first — streaming straight from getStream() over
  // WiFiClientSecure can return IncompleteInput when the TLS buffer drains
  // mid-parse, especially on the larger combined response.
  String response = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, response);
  if (error) {
    DBG("Open-Meteo JSON error: "); DBGLN(error.c_str());
    DBG("Response length: "); DBGLN(response.length());
    return false;
  }

  // 24-hour temperature curve for the dry-state right panel.
  JsonArray hourly = doc["hourly"]["temperature_2m"];
  if (!hourly.isNull()) {
    int n = min(24, (int)hourly.size());
    for (int i = 0; i < n; i++) {
      extras.hourlyTemp[i] = hourly[i] | 0.0f;
    }
    extras.hourlyCount = n;
  }

  // Daily forecast
  JsonArray tmax = doc["daily"]["temperature_2m_max"];
  JsonArray tmin = doc["daily"]["temperature_2m_min"];
  JsonArray rainProb = doc["daily"]["precipitation_probability_max"];
  JsonArray wcode = doc["daily"]["weather_code"];
  JsonArray sunshine = doc["daily"]["sunshine_duration"];
  JsonArray daylight = doc["daily"]["daylight_duration"];
  JsonArray precipSum = doc["daily"]["precipitation_sum"];
  JsonArray precipHours = doc["daily"]["precipitation_hours"];
  JsonArray snowfall = doc["daily"]["snowfall_sum"];
  JsonArray dates = doc["daily"]["time"];
  JsonArray sunriseArr = doc["daily"]["sunrise"];
  JsonArray sunsetArr  = doc["daily"]["sunset"];

  forecastCount = min(7, (int)tmax.size());
  const char* dayNames[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};

  for (int i = 0; i < forecastCount; i++) {
    forecast[i].tempMax = (int)(tmax[i].as<float>());
    forecast[i].tempMin = (int)(tmin[i].as<float>());
    forecast[i].rainProb = rainProb[i] | 0;

    int apiCode = wcode[i] | 0;
    float sunHours = sunshine[i].as<float>() / 3600.0f;
    float dayHours = daylight[i].as<float>() / 3600.0f;
    float precip = precipSum[i] | 0.0f;
    int precipH = precipHours[i] | 0;
    float snow = snowfall[i] | 0.0f;

    bool useSunnyVariant;
    forecast[i].category = calculateDailyWeather(
      apiCode, precip, precipH, snow, sunHours, dayHours, useSunnyVariant);
    forecast[i].useSunnyVariant = useSunnyVariant;

    // Sunrise / sunset: extract "HH:MM" from "YYYY-MM-DDTHH:MM"
    forecast[i].sunrise[0] = 0;
    forecast[i].sunset[0]  = 0;
    const char* sr = sunriseArr[i].as<const char*>();
    const char* ss = sunsetArr[i].as<const char*>();
    if (sr && strlen(sr) >= 16) { memcpy(forecast[i].sunrise, sr + 11, 5); forecast[i].sunrise[5] = 0; }
    if (ss && strlen(ss) >= 16) { memcpy(forecast[i].sunset,  ss + 11, 5); forecast[i].sunset[5]  = 0; }

    if (i == 0) {
      strlcpy(forecast[i].dayName, "Today", sizeof(forecast[i].dayName));
    } else {
      const char* dateStr = dates[i].as<const char*>();
      if (dateStr && strlen(dateStr) >= 10) {
        struct tm t = {};
        char buf[5];
        strncpy(buf, dateStr, 4); buf[4] = 0; t.tm_year = atoi(buf) - 1900;
        strncpy(buf, dateStr + 5, 2); buf[2] = 0; t.tm_mon = atoi(buf) - 1;
        strncpy(buf, dateStr + 8, 2); buf[2] = 0; t.tm_mday = atoi(buf);
        mktime(&t);
        strlcpy(forecast[i].dayName, dayNames[t.tm_wday], sizeof(forecast[i].dayName));
      } else {
        strlcpy(forecast[i].dayName, "?", sizeof(forecast[i].dayName));
      }
    }
  }

  return true;
}

// ============================================================================
// BUIENRADAR — live KNMI station observations + radar nowcast
// ============================================================================

// The feed exposes the weather icon as a URL (".../weather/30x30/aa.png")
// rather than a structured code field. Extract the filename stem, lowercase
// it, and collapse doubled letters ("aa" → "a") so categorizeBuienradarIcon
// sees a single-letter input. "cc" is kept as-is because it's a distinct
// entry in the mapping table.
// Writes "" into `out` if the URL doesn't match the expected shape.
static void extractBuienradarIconCode(const char* iconurl, char* out, size_t outSize) {
  if (outSize > 0) out[0] = 0;
  if (!iconurl || outSize < 2) return;
  const char* slash = strrchr(iconurl, '/');
  const char* dot   = strrchr(iconurl, '.');
  if (!slash || !dot || dot <= slash + 1) return;
  size_t len = (size_t)(dot - slash - 1);
  if (len == 0 || len >= outSize) return;
  memcpy(out, slash + 1, len);
  out[len] = 0;
  for (size_t i = 0; out[i]; i++) {
    if (out[i] >= 'A' && out[i] <= 'Z') out[i] += 32;
  }
  if (out[0] && out[1] == out[0] && out[2] == 0 && out[0] != 'c') {
    out[1] = 0;
  }
}

// Fetch the master JSON feed, pick the closest non-stale station with a
// usable icon, and return its current readings translated into the
// dashboard's internal types. Failure leaves the output params untouched
// so the caller's RTC cache fallback can take over.
//
// Picker: nearest-station-with-outlier-rejection. Collect up to
// BUIENRADAR_MAX_CANDIDATES nearest valid stations, then within
// BUIENRADAR_CONSENSUS_KM take the mode of the icon category (tie → closest).
// Temperature / wind / icon are then taken from the closest station that
// voted for the winning category, so we never display readings from a
// station whose icon we just outvoted. See CLAUDE.md "Buienradar consensus
// picker" for the caveats — single-station outliers like Voorschoten reporting
// OVERCAST while all neighbours report CLEAR motivated this; the approach
// will lag by one wake during frontal passages and is worth revisiting once
// real disagreement data is logged.
bool fetchBuienradarNow(float &temp, float &wind,
                        WeatherCategory &category, int &windBearing) {
  DBG("BR now heap before: "); DBGLN(ESP.getFreeHeap());

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);

  if (!http.begin(client, BUIENRADAR_FEED_URL)) return false;

  int code = httpGetWithRetry(http);
  if (code != 200) {
    DBG("BR now HTTP fail: "); DBGLN(code);
    http.end();
    return false;
  }

  String response = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, response);
  response = String();
  if (err) {
    DBG("BR now JSON err: "); DBGLN(err.c_str());
    return false;
  }

  JsonArray stations = doc["actual"]["stationmeasurements"];
  if (stations.isNull()) {
    DBGLN("BR now: no stationmeasurements[]");
    return false;
  }

  // Find current time for the freshness filter. Both device and Buienradar
  // run on Amsterdam local time, so wall-clock comparison is fine.
  struct tm tnow;
  bool haveNow = getLocalTime(&tnow);
  time_t nowEpoch = haveNow ? mktime(&tnow) : 0;

  struct Cand {
    float dist;
    WeatherCategory cat;
    float temp;
    float ms;
    int bearing;
    char icon[4];
    char name[24];
  };
  Cand cands[BUIENRADAR_MAX_CANDIDATES];
  int nCands = 0;

  for (JsonObject s : stations) {
    char iconBuf[4];
    extractBuienradarIconCode(s["iconurl"] | (const char*)nullptr,
                              iconBuf, sizeof(iconBuf));
    if (!iconBuf[0]) continue;
    if (s["temperature"].isNull()) continue;

    if (haveNow) {
      time_t tsEpoch = parseISOToLocal(s["timestamp"] | (const char*)nullptr);
      if (tsEpoch > 0 &&
          (nowEpoch - tsEpoch) > (long)BUIENRADAR_STALE_MIN * 60) continue;
    }

    float lat = s["lat"] | 0.0f;
    float lon = s["lon"] | 0.0f;
    float d = haversineKm(LATITUDE, LONGITUDE, lat, lon);

    // Insertion-sort into a fixed-size buffer kept sorted by distance.
    if (nCands == BUIENRADAR_MAX_CANDIDATES &&
        d >= cands[BUIENRADAR_MAX_CANDIDATES - 1].dist) continue;

    int pos = nCands < BUIENRADAR_MAX_CANDIDATES ? nCands
                                                 : BUIENRADAR_MAX_CANDIDATES - 1;
    while (pos > 0 && cands[pos - 1].dist > d) {
      cands[pos] = cands[pos - 1];
      pos--;
    }

    Cand &c = cands[pos];
    c.dist = d;
    strncpy(c.icon, iconBuf, sizeof(c.icon));
    c.icon[sizeof(c.icon) - 1] = 0;
    c.cat = categorizeBuienradarIcon(c.icon);
    c.temp = s["temperature"] | 0.0f;
    c.ms   = s["windspeed"]   | 0.0f;
    int b = s["winddirectiondegrees"] | -1;
    if (b < 0) {
      b = bearingFromDutchCardinal(s["winddirection"] | "");
      if (b < 0) b = 0;
    }
    c.bearing = b;
    const char* nm = s["stationname"] | "";
    strncpy(c.name, nm, sizeof(c.name));
    c.name[sizeof(c.name) - 1] = 0;

    if (nCands < BUIENRADAR_MAX_CANDIDATES) nCands++;
  }

  if (nCands == 0) {
    DBGLN("BR now: no usable station");
    return false;
  }

  // Mode of icon category across stations within the consensus radius.
  // If only one station is in range we degrade gracefully to "nearest wins".
  const int NUM_CATS = THUNDERSTORM + 1;
  int votes[NUM_CATS] = {0};
  int inRange = 0;
  for (int i = 0; i < nCands; i++) {
    if (cands[i].dist <= BUIENRADAR_CONSENSUS_KM) {
      votes[cands[i].cat]++;
      inRange++;
    }
  }

  int maxVotes = 0;
  for (int c = 0; c < NUM_CATS; c++) if (votes[c] > maxVotes) maxVotes = votes[c];

  WeatherCategory winningCat = cands[0].cat;  // default to nearest
  if (maxVotes > 0) {
    // Tiebreak by closest: cands is sorted, so first hit at maxVotes wins.
    for (int i = 0; i < nCands; i++) {
      if (cands[i].dist <= BUIENRADAR_CONSENSUS_KM &&
          votes[cands[i].cat] == maxVotes) {
        winningCat = cands[i].cat;
        break;
      }
    }
  }

  // Source the displayed numbers from the closest in-range voter for the
  // winning category. Falls back to the nearest candidate overall.
  int srcIdx = 0;
  for (int i = 0; i < nCands; i++) {
    if (cands[i].cat == winningCat &&
        cands[i].dist <= BUIENRADAR_CONSENSUS_KM) {
      srcIdx = i;
      break;
    }
  }

  Cand &src = cands[srcIdx];
  temp        = src.temp;
  wind        = src.ms * 3.6f;
  category    = src.cat;
  windBearing = src.bearing;

  DBG("BR now picker: src="); DBG(src.name);
  DBG(" ("); DBG(src.dist); DBG(" km) code="); DBG(src.icon);
  DBG(" inRange="); DBG(inRange); DBG("/"); DBGLN(nCands);
#if DEBUG_LOG
  for (int i = 0; i < nCands; i++) {
    DBG("  cand["); DBG(i); DBG("] "); DBG(cands[i].name);
    DBG(" "); DBG(cands[i].dist); DBG("km icon="); DBG(cands[i].icon);
    DBG(" cat="); DBGLN((int)cands[i].cat);
  }
#endif
  return true;
}

// Fetch the hyper-local 2h rain nowcast (24 lines of "VVV|HH:MM" at 5-min
// spacing). Converts each line to mm/h via the published log formula.
// On success, writes up to 24 entries into `rainData` and the matching
// "HH:MM\0" strings into `timeLabels[i]` (caller allocates [][6]).
bool fetchBuienradarRain(float rainData[], char timeLabels[][6], int &rainCount) {
  rainCount = 0;

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);

  char url[160];
  snprintf(url, sizeof(url), BUIENRADAR_RAIN_URL,
           (double)LATITUDE, (double)LONGITUDE);

  if (!http.begin(client, url)) return false;

  int code = httpGetWithRetry(http);
  if (code != 200) {
    DBG("BR rain HTTP fail: "); DBGLN(code);
    http.end();
    return false;
  }

  String body = http.getString();
  http.end();

  if (body.length() < 5) {
    DBGLN("BR rain: empty body");
    return false;
  }

  int idx = 0;
  int from = 0;
  while (idx < 24 && from < (int)body.length()) {
    int nl = body.indexOf('\n', from);
    int lineEnd = (nl < 0) ? body.length() : nl;
    int pipe = body.indexOf('|', from);
    if (pipe > from && pipe < lineEnd && (lineEnd - pipe) >= 6) {
      int v = body.substring(from, pipe).toInt();
      float mmh = (v <= 0) ? 0.0f : powf(10.0f, ((float)v - 109.0f) / 32.0f);
      rainData[idx] = mmh;
      // "HH:MM" lives immediately after the pipe.
      strlcpy(timeLabels[idx], body.substring(pipe + 1, pipe + 6).c_str(), sizeof(timeLabels[idx]));
      idx++;
    }
    if (nl < 0) break;
    from = nl + 1;
  }

  rainCount = idx;
  DBG("BR rain: parsed "); DBG(rainCount); DBGLN(" lines");
  return (rainCount > 0);
}

// NS Trip Planner v3: fetch up to maxCount trips from fromStation to toStation.
// Streams the response through ArduinoJson with a Filter so the parsed tree
// stays small (~5 KB) instead of holding the full ~90 KB payload in heap.
// Per CLAUDE.md, streaming over WiFiClientSecure was flaky for the merged
// Open-Meteo call — being re-validated here on a similarly large payload.
int fetchTrips(const char* fromStation, const char* toStation,
               TrainOrigin origin, Departure out[], int maxCount) {
  DBG("fetchTrips heap before: "); DBGLN(ESP.getFreeHeap());

  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  http.setTimeout(8000);

  char url[256];
  snprintf(url, sizeof(url),
    "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips"
    "?fromStation=%s&toStation=%s&maxJourneys=%d",
    fromStation, toStation, maxCount);

  if (!http.begin(client, url)) {
    DBGLN("ERROR: http.begin() failed (Trip Planner)");
    return 0;
  }
  http.addHeader("Ocp-Apim-Subscription-Key", NS_API_KEY);

  int code = httpGetWithRetry(http);
  if (code != 200) {
    DBG("Trip Planner HTTP fail: "); DBGLN(code);
    http.end();
    return 0;
  }

  // Filter: only the fields the picker + card renderer need.
  // [0] on arrays applies the filter to all array elements.
  JsonDocument filter;
  JsonObject leg = filter["trips"][0]["legs"][0].to<JsonObject>();
  leg["cancelled"] = true;
  leg["partCancelled"] = true;
  JsonObject lo = leg["origin"].to<JsonObject>();
  lo["plannedDateTime"] = true;
  lo["actualDateTime"] = true;
  lo["plannedTrack"] = true;
  JsonObject ld = leg["destination"].to<JsonObject>();
  ld["plannedDateTime"] = true;
  ld["actualDateTime"] = true;

  // Slurp the full body into a String before parsing. Streaming straight
  // from getStream() over WiFiClientSecure intermittently returns
  // IncompleteInput when the TLS buffer drains mid-parse on the ~90 KB
  // Trip Planner payload — same issue that pushed fetchOpenMeteo to slurp.
  // The Filter still keeps the parsed JsonDoc small (~5 KB) so heap peak
  // is dominated by the transient String, not the parse tree.
  String response = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, response,
                                             DeserializationOption::Filter(filter));
  response = String();  // free the ~90 KB buffer immediately

  if (err) {
    DBG("Trip Planner JSON err: "); DBGLN(err.c_str());
    return 0;
  }

  JsonArray trips = doc["trips"];
  if (trips.isNull()) {
    DBGLN("Trip Planner: no trips[] in response");
    return 0;
  }

  int found = 0;
  for (JsonObject trip : trips) {
    if (found >= maxCount) break;
    JsonArray legs = trip["legs"];
    if (legs.isNull() || legs.size() == 0) continue;

    JsonObject firstLeg = legs[0];
    JsonObject lastLeg  = legs[legs.size() - 1];

    const char* planned = firstLeg["origin"]["plannedDateTime"] | (const char*)nullptr;
    const char* actual  = firstLeg["origin"]["actualDateTime"]  | (const char*)nullptr;
    const char* track   = firstLeg["origin"]["plannedTrack"]    | "?";

    if (!planned || strlen(planned) < 16) continue;

    Departure& d = out[found];
    d.origin = origin;
    d.note[0] = 0;  // picker may overwrite in a later step

    // Departure time: prefer actual, fall back to planned. Empty actual does
    // NOT mean cancelled — it means no realtime data yet (e.g. trip far ahead).
    const char* useTime = (actual && strlen(actual) >= 16) ? actual : planned;
    memcpy(d.time, useTime + 11, 5);
    d.time[5] = 0;

    strlcpy(d.track, track, sizeof(d.track));

    bool firstCancelled = (firstLeg["cancelled"]     | false) ||
                          (firstLeg["partCancelled"] | false);
    d.cancelled = firstCancelled;

    d.delay[0] = 0;
    if (!firstCancelled && actual && strlen(actual) >= 16) {
      int delayMin = calculateDelay(planned, actual);
      if (delayMin >= 1) {
        snprintf(d.delay, sizeof(d.delay), "+%dm", delayMin);
      }
    }

    // Uni arrival from the final leg's destination
    const char* arrPlanned = lastLeg["destination"]["plannedDateTime"] | (const char*)nullptr;
    const char* arrActual  = lastLeg["destination"]["actualDateTime"]  | (const char*)nullptr;
    const char* useArr = (arrActual && strlen(arrActual) >= 16) ? arrActual : arrPlanned;
    d.uniArr[0] = 0;
    if (useArr && strlen(useArr) >= 16) {
      memcpy(d.uniArr, useArr + 11, 5);
      d.uniArr[5] = 0;
    }

    // Transfer status from the final leg (the Breda→TBU sprinter).
    // Spike showed all DH→TBU trips have 2 legs; single-leg branch is
    // defensive for unexpected shapes.
    bool lastCancelled = (lastLeg["cancelled"]     | false) ||
                         (lastLeg["partCancelled"] | false);
    if (legs.size() == 1) {
      d.transfer = TRANSFER_OK;
    } else if (lastCancelled) {
      d.transfer = TRANSFER_CANCELLED;
    } else {
      const char* lp = lastLeg["origin"]["plannedDateTime"] | (const char*)nullptr;
      const char* la = lastLeg["origin"]["actualDateTime"]  | (const char*)nullptr;
      if (lp && la && strlen(lp) >= 16 && strlen(la) >= 16 &&
          calculateDelay(lp, la) >= 5) {
        d.transfer = TRANSFER_LATE;
      } else {
        d.transfer = TRANSFER_OK;
      }
    }

    strlcpy(d.plannedDepartureISO, planned, sizeof(d.plannedDepartureISO));

    found++;
  }

  DBG("fetchTrips from "); DBG(fromStation); DBG(": parsed "); DBGLN(found);
  DBG("fetchTrips heap after:  "); DBGLN(ESP.getFreeHeap());
  return found;
}

