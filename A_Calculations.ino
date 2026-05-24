// ============================================================================
// CALCULATION FUNCTIONS
// ============================================================================
// Weather analysis and time calculations

// ============================================================================
// WEATHER CODE CALCULATION
// ============================================================================

// Calculate daily weather category from raw API data
WeatherCategory calculateDailyWeather(
  int apiWeatherCode,
  float precipSum,
  int precipHours,
  float snowfallSum,
  float sunshineHours,
  float daylightHours,
  bool &useSunnyVariant
) {
  // Priority 1: Check API code for special events (fog and thunderstorm)
  // These can't be reliably detected from daily aggregates
  if (apiWeatherCode == 45 || apiWeatherCode == 48) {
    useSunnyVariant = false;
    return FOG;
  }
  if (apiWeatherCode >= 95) {
    useSunnyVariant = false;
    return THUNDERSTORM;
  }
  
  // Priority 2: Snow detection
  if (snowfallSum >= 1.0) {
    useSunnyVariant = (daylightHours > 0 && (sunshineHours / daylightHours) >= 0.5);
    return SNOW;
  }
  
  // Calculate sunshine ratio
  float sunshineRatio = (daylightHours > 0) ? (sunshineHours / daylightHours) : 0;
  
  // Priority 3: Precipitation-based codes
  // Special case: Sunny day with brief showers
  if (sunshineRatio >= 0.5 && precipSum < 5.0 && precipSum > 0) {
    useSunnyVariant = false;
    return PARTLY_CLOUDY;
  }
  
  // Normal precipitation hierarchy
  if (precipSum >= 10.0) {
    useSunnyVariant = sunshineRatio >= 0.5;
    return RAIN_HEAVY;
  }
  if (precipSum >= 5.0) {
    useSunnyVariant = sunshineRatio >= 0.5;
    return RAIN;
  }
  if (precipSum >= 1.0 || precipHours >= 3) {
    useSunnyVariant = sunshineRatio >= 0.5;
    return DRIZZLE;
  }
  
  // Priority 4: Dry days - use sunshine ratio
  useSunnyVariant = false;
  if (sunshineRatio >= 0.65) {
    return CLEAR;
  }
  if (sunshineRatio >= 0.35) {
    return PARTLY_CLOUDY;
  }
  return OVERCAST;
}

// ============================================================================
// TIME CALCULATIONS
// ============================================================================

// Parse Trip Planner ISO 8601 ("2026-05-24T12:19:00+0200") into a time_t
// in the device's local timezone. Both Trip Planner and the device clock
// run on Amsterdam time (configTzTime + timezone=auto on Open-Meteo), so
// treating the wall-clock fields as local — without applying the +0200
// offset — yields the correct epoch. tm_isdst=-1 lets mktime decide DST.
static time_t parseISOToLocal(const char* iso) {
  if (!iso || strlen(iso) < 19) return 0;
  struct tm t = {};
  t.tm_year = (iso[0]-'0')*1000 + (iso[1]-'0')*100 + (iso[2]-'0')*10 + (iso[3]-'0') - 1900;
  t.tm_mon  = (iso[5]-'0')*10 + (iso[6]-'0') - 1;
  t.tm_mday = (iso[8]-'0')*10 + (iso[9]-'0');
  t.tm_hour = (iso[11]-'0')*10 + (iso[12]-'0');
  t.tm_min  = (iso[14]-'0')*10 + (iso[15]-'0');
  t.tm_sec  = (iso[17]-'0')*10 + (iso[18]-'0');
  t.tm_isdst = -1;
  return mktime(&t);
}

// "+8m" -> 8, "+12m" -> 12, "" -> 0.
static int parseDelayMin(const char* s) {
  if (!s || !s[0]) return 0;
  const char* p = s;
  if (*p == '+') p++;
  return atoi(p);
}

// Per-slot train picker. For each of 3 slots, take the next un-picked
// Centraal trip; if it's healthy (not cancelled, delay < 10 min) use it.
// Otherwise look for an HS trip departing within ±20 min of the Centraal's
// planned time, not cancelled, and at least 5 min in the future so the user
// can actually get to HS. Falls back to the bad Centraal trip if no HS
// alternative exists. Adjacent duplicate trips (same plannedDepartureISO)
// are skipped — NS returns multiple routing options for the same physical
// train.
int pickDepartures(const Departure ctr[], int nCtr,
                   const Departure hs[],  int nHs,
                   Departure out[3]) {
  time_t now = time(nullptr);
  time_t prevPickTime = 0;

  bool ctrUsed[12] = {};
  bool hsUsed[12]  = {};

  // Fallback: when the Centraal fetch returned 0 trips (e.g. transient
  // Trip Planner failure), promote HS to primary so the Departures section
  // still has content. Picker's normal substitution logic only triggers on
  // *bad* CTR trips — with no CTR trips at all, the outer loop below
  // exits immediately and the section would render empty even though HS
  // data is right here.
  if (nCtr == 0) {
    int picked = 0;
    for (int i = 0; i < nHs && i < 12 && picked < 3; i++) {
      if (hs[i].cancelled) continue;
      time_t t = parseISOToLocal(hs[i].plannedDepartureISO);
      if (t == 0 || t < now + 5 * 60) continue;            // must be reachable
      if (picked > 0 &&
          strcmp(hs[i].plannedDepartureISO,
                 out[picked - 1].plannedDepartureISO) == 0) continue;  // dedup
      out[picked] = hs[i];
      strlcpy(out[picked].note, "Centraal unavailable", sizeof(out[picked].note));
      picked++;
    }
    return picked;
  }

  int picked = 0;
  while (picked < 3) {
    // Find next un-picked Centraal trip departing after the previous pick.
    int ctrIdx = -1;
    time_t ctrTime = 0;
    for (int i = 0; i < nCtr && i < 12; i++) {
      if (ctrUsed[i]) continue;
      time_t t = parseISOToLocal(ctr[i].plannedDepartureISO);
      if (t == 0 || t <= prevPickTime) continue;
      ctrIdx  = i;
      ctrTime = t;
      break;
    }
    if (ctrIdx < 0) break;

    // Dedup: mark every entry with the same plannedDepartureISO as used.
    for (int i = 0; i < nCtr && i < 12; i++) {
      if (strcmp(ctr[i].plannedDepartureISO, ctr[ctrIdx].plannedDepartureISO) == 0) {
        ctrUsed[i] = true;
      }
    }

    int ctrDelay = parseDelayMin(ctr[ctrIdx].delay);
    bool ctrGood = !ctr[ctrIdx].cancelled && ctrDelay < 10;

    if (ctrGood) {
      out[picked] = ctr[ctrIdx];
      out[picked].note[0] = 0;
    } else {
      // Look for an HS substitute around the same time.
      int hsIdx = -1;
      for (int i = 0; i < nHs && i < 12; i++) {
        if (hsUsed[i] || hs[i].cancelled) continue;
        time_t t = parseISOToLocal(hs[i].plannedDepartureISO);
        if (t == 0) continue;
        long deltaMin = (long)((t - ctrTime) / 60);
        if (deltaMin < -20 || deltaMin > 20) continue;
        if (t < now + 5 * 60) continue;          // must be reachable
        hsIdx = i;
        break;
      }

      if (hsIdx >= 0) {
        for (int i = 0; i < nHs && i < 12; i++) {
          if (strcmp(hs[i].plannedDepartureISO, hs[hsIdx].plannedDepartureISO) == 0) {
            hsUsed[i] = true;
          }
        }
        out[picked] = hs[hsIdx];
        if (ctr[ctrIdx].cancelled) {
          strlcpy(out[picked].note, "Centraal cancelled", sizeof(out[picked].note));
        } else {
          snprintf(out[picked].note, sizeof(out[picked].note),
                   "Centraal %s", ctr[ctrIdx].delay);
        }
      } else {
        // No HS alternative — show the disrupted Centraal anyway.
        out[picked] = ctr[ctrIdx];
        out[picked].note[0] = 0;
      }
    }

    prevPickTime = ctrTime;  // anchor next slot on the CTR timeline
    picked++;
  }

  return picked;
}

// ============================================================================
// BUIENRADAR HELPERS
// ============================================================================

// Haversine great-circle distance in km. Float math is plenty accurate for
// ranking the ~50 weather stations the BR feed exposes.
float haversineKm(float lat1, float lon1, float lat2, float lon2) {
  const float R = 6371.0f;
  float dLat = (lat2 - lat1) * (float)PI / 180.0f;
  float dLon = (lon2 - lon1) * (float)PI / 180.0f;
  float a = sinf(dLat / 2.0f) * sinf(dLat / 2.0f) +
            cosf(lat1 * (float)PI / 180.0f) * cosf(lat2 * (float)PI / 180.0f) *
            sinf(dLon / 2.0f) * sinf(dLon / 2.0f);
  float c = 2.0f * atan2f(sqrtf(a), sqrtf(1.0f - a));
  return R * c;
}

// Buienradar uses Dutch 16-point compass strings ("N", "NNO", "ZZW", …).
// Returns degrees from north (0–337) or -1 if unrecognised.
// Dutch convention: O=Ost(east), Z=Zuid(south), W=West, N=Noord.
int bearingFromDutchCardinal(const char* s) {
  if (!s || !s[0]) return -1;
  struct { const char* code; int deg; } table[] = {
    {"N",0},{"NNO",23},{"NO",45},{"ONO",68},
    {"O",90},{"OZO",113},{"ZO",135},{"ZZO",158},
    {"Z",180},{"ZZW",203},{"ZW",225},{"WZW",248},
    {"W",270},{"WNW",293},{"NW",315},{"NNW",338}
  };
  for (auto& e : table) if (strcmp(s, e.code) == 0) return e.deg;
  return -1;
}

// Map a Buienradar single/double-letter iconcode to the existing
// WeatherCategory enum. See docs/buienradar-integration-plan.md §B2 for the
// derivation. Unknown codes fall through to OVERCAST.
WeatherCategory categorizeBuienradarIcon(const char* code, bool& useSunnyVariant) {
  useSunnyVariant = false;
  if (!code || !code[0]) return OVERCAST;

  // "cc" is the only multi-char code; handle it explicitly.
  if (strcmp(code, "cc") == 0) return OVERCAST;

  switch (code[0]) {
    case 'a': return CLEAR;
    case 'j': return CLEAR;
    case 'b': return PARTLY_CLOUDY;
    case 'o': return PARTLY_CLOUDY;
    case 'r': return PARTLY_CLOUDY;
    case 'p': return OVERCAST;
    case 'c': return OVERCAST;
    case 'd': return FOG;
    case 'n': return FOG;
    case 'f': useSunnyVariant = true;  return DRIZZLE;
    case 'k': return DRIZZLE;
    case 'h': useSunnyVariant = true;  return RAIN;
    case 'm': return RAIN_HEAVY;
    case 'q': return RAIN_HEAVY;
    case 'g': return THUNDERSTORM;
    case 's': return THUNDERSTORM;
    case 'l': return THUNDERSTORM;
    case 'i': return SNOW;
    case 'u': useSunnyVariant = true;  return SNOW;
    case 'v': return SNOW;
    case 'w': return SNOW;
    default:
      DBG("BR unknown iconcode: "); DBGLN(code);
      return OVERCAST;
  }
}

// Synthetic WMO code so the existing categorizeCurrentWeather(int) round-trips
// a Buienradar-derived category back to the same enum value (option α in the
// integration plan). Lets us inject BR data without changing every signature
// from fetch to drawWeatherIcon128.
int categoryToSyntheticWmo(WeatherCategory c) {
  switch (c) {
    case CLEAR:         return 0;
    case PARTLY_CLOUDY: return 2;
    case OVERCAST:      return 3;
    case FOG:           return 45;
    case DRIZZLE:       return 51;
    case RAIN:          return 61;
    case RAIN_HEAVY:    return 81;
    case SNOW:          return 71;
    case THUNDERSTORM:  return 95;
  }
  return 3;
}

// 8-bucket cardinal compass from a meteorological wind bearing (degrees from N,
// clockwise). 0° → "N", 45° → "NE", etc. Buckets are 45° wide and centered on
// the cardinal/inter-cardinal points (so 23–67° is "NE").
const char* cardinalCompass(int bearingDeg) {
  static const char* labels[] = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
  int b = ((bearingDeg % 360) + 360) % 360;  // normalise into [0, 360)
  int idx = (int)(((float)b + 22.5f) / 45.0f) % 8;
  return labels[idx];
}

// Calculate delay in minutes from ISO 8601 timestamps.
// Handles cross-midnight: if actualTotal < plannedTotal assume the actual time
// wrapped past midnight (e.g. planned 23:58, actual 00:02 → +4 min).
int calculateDelay(const char* planned, const char* actual) {
  if (!planned || !actual) return 0;
  if (strlen(planned) < 16 || strlen(actual) < 16) return 0;

  auto twoDigit = [](const char* s, int off) {
    return (s[off] - '0') * 10 + (s[off + 1] - '0');
  };
  int plannedH = twoDigit(planned, 11);
  int plannedM = twoDigit(planned, 14);
  int actualH  = twoDigit(actual, 11);
  int actualM  = twoDigit(actual, 14);

  int plannedTotal = plannedH * 60 + plannedM;
  int actualTotal  = actualH  * 60 + actualM;

  int delay = actualTotal - plannedTotal;
  if (delay < -120) delay += 24 * 60;  // crossed midnight
  return delay;
}