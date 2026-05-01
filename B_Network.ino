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
    DayForecast forecast[],
    int &forecastCount,
    float rainData[],
    char timeLabels[][20],
    int &rainCount,
    bool &rainOk
  ) {
  rainOk = false;
  rainCount = 0;

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);

  char url[512];
  snprintf(url, sizeof(url),
    "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
    "&current=temperature_2m,weather_code,wind_speed_10m"
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

int getTrains(Train trains[], int max, const char* stationCode) {
  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  http.setTimeout(8000);
  String url = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v2/departures?station=" + String(stationCode);

  if (!http.begin(client, url)) {
    DBGLN("ERROR: http.begin() failed");
    return 0;
  }
  
  http.addHeader("Ocp-Apim-Subscription-Key", NS_API_KEY);

  int httpCode = httpGetWithRetry(http);
  if (httpCode != 200) {
    DBG("Train API failed with code: "); DBGLN(httpCode);
    http.end();
    return 0;
  }
  
  String response = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, response);

  if (error) {
    DBG("Train JSON parse error: "); DBGLN(error.c_str());
    DBG("Response length: "); DBGLN(response.length());
    return 0;
  }

  JsonArray deps = doc["payload"]["departures"];
  if (deps.isNull()) {
    DBGLN("No departures array in response");
    return 0;
  }

  int found = 0;

  for (JsonObject dep : deps) {
    if (found >= max) break;

    // Check if destination is in the route (case-insensitive substring)
    JsonArray routeStations = dep["routeStations"];
    bool stopsAtDest = false;

    for (JsonObject station : routeStations) {
      const char* name = station["mediumName"].as<const char*>();
      if (name && strcasestr(name, TRAIN_DESTINATION) != nullptr) {
        stopsAtDest = true;
        break;
      }
    }

    if (stopsAtDest) {
      const char* planned = dep["plannedDateTime"].as<const char*>();
      const char* actual  = dep["actualDateTime"]  | (const char*)nullptr;
      const char* track   = dep["plannedTrack"]    | "?";

      if (!planned || strlen(planned) < 16) continue;

      // time: "HH:MM" from chars 11..15
      memcpy(trains[found].time, planned + 11, 5);
      trains[found].time[5] = 0;

      strlcpy(trains[found].track, track, sizeof(trains[found].track));
      trains[found].cancelled = dep["cancelled"] | false;

      trains[found].delay[0] = 0;
      if (actual && strlen(actual) >= 16) {
        int delayMin = calculateDelay(planned, actual);
        if (delayMin >= 2) {
          snprintf(trains[found].delay, sizeof(trains[found].delay), "+%dm", delayMin);
        }
      }

      found++;
    }
  }
  
  DBG("Station "); DBG(stationCode);
  DBG(": Found "); DBG(found);
  DBGLN(" trains stopping at destination");
  
  return found;
}