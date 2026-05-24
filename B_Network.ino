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

// Combined Open-Meteo call: current weather + 7-day forecast + 15-min rain.
// One TLS handshake, one JSON parse. rainOk is set independently of the
// overall return so a missing minutely block doesn't fail the whole fetch.
bool fetchOpenMeteo(
    float &temp,
    float &wind,
    int &currentWeatherCode,
    WeatherExtras &extras,
    DayForecast forecast[],
    int &forecastCount,
    float rainData[],
    char timeLabels[][20],
    int &rainCount,
    bool &rainOk
  ) {
  rainOk = false;
  rainCount = 0;
  extras.windDirection = 0;
  extras.hourlyCount = 0;

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);

  char url[640];
  snprintf(url, sizeof(url),
    "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
    "&current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m"
    "&hourly=temperature_2m&forecast_hours=24"
    "&minutely_15=precipitation&forecast_minutely_15=12"
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

  // Current weather
  temp = doc["current"]["temperature_2m"] | 0.0f;
  wind = doc["current"]["wind_speed_10m"] | 0.0f;
  currentWeatherCode = doc["current"]["weather_code"] | 0;

  extras.windDirection = doc["current"]["wind_direction_10m"] | 0;

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

  // 15-min rain
  JsonArray rain = doc["minutely_15"]["precipitation"];
  JsonArray times = doc["minutely_15"]["time"];
  if (!rain.isNull() && !times.isNull()) {
    rainCount = min(12, (int)rain.size());
    for (int i = 0; i < rainCount; i++) {
      rainData[i] = rain[i] | 0.0f;
      const char* t = times[i].as<const char*>();
      if (t) strlcpy(timeLabels[i], t, 20);
      else   timeLabels[i][0] = 0;
    }
    rainOk = (rainCount > 0);
  }

  return true;
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

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getStream(),
                                             DeserializationOption::Filter(filter));
  http.end();

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

