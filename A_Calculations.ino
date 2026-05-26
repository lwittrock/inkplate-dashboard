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
time_t parseISOToLocal(const char* iso) {
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

// Decide whether the picker is likely to need HS substitution data.
// True iff any of the first 5 Centraal trips is cancelled or significantly
// delayed (matches picker's substitution trigger in pickDepartures), OR if
// CTR returned zero trips (picker promotes HS to primary). The 5-trip
// window matches the dedup-aware lookahead — picker only ever uses 3 slots
// but the window has to be wider because NS returns duplicates.
// Used to gate the cached-HS path: clean CTR → cache OK; anything else →
// always re-fetch GV so substitutions use fresh data.
bool ctrHasDisruption(const Departure list[], int n) {
  if (n == 0) return true;
  int cap = n < 5 ? n : 5;
  for (int i = 0; i < cap; i++) {
    if (list[i].cancelled) return true;
    if (parseDelayMin(list[i].delay) >= 10) return true;
  }
  return false;
}

// Remove "dominated" trips in place: a trip is dominated when another trip
// in the same list departs strictly later AND arrives no later. Such trips
// are strictly worse — you'd always take the later-departing one. NS
// sometimes returns slow routings (extra stops, indirect path) alongside
// the fast direct trip; this strips them before the picker sees them.
//
// Compares d.time / d.uniArr "HH:MM" strings — strcmp orders correctly
// within the dashboard's active window (night mode sleeps through midnight).
// Cancelled trips never dominate (a cancelled "later" trip can't legitimately
// replace an earlier one) and are never themselves dropped via dominance
// (the picker needs to see them to surface the disruption).
int filterDominatedTrips(Departure list[], int n) {
  if (n <= 1) return n;
  bool drop[12] = {};
  int cap = n < 12 ? n : 12;
  for (int i = 0; i < cap; i++) {
    if (list[i].cancelled) continue;
    if (!list[i].time[0] || !list[i].uniArr[0]) continue;
    for (int j = 0; j < cap; j++) {
      if (i == j || list[j].cancelled) continue;
      if (!list[j].time[0] || !list[j].uniArr[0]) continue;
      if (strcmp(list[j].time, list[i].time) > 0 &&
          strcmp(list[j].uniArr, list[i].uniArr) <= 0) {
        DBG("  drop dominated trip "); DBG(list[i].time);
        DBG("->"); DBG(list[i].uniArr);
        DBG(" (beaten by "); DBG(list[j].time);
        DBG("->"); DBG(list[j].uniArr); DBGLN(")");
        drop[i] = true;
        break;
      }
    }
  }
  int kept = 0;
  for (int i = 0; i < cap; i++) {
    if (drop[i]) continue;
    if (kept != i) list[kept] = list[i];
    kept++;
  }
  return kept;
}

// Per-slot train picker.
//
// Policy:
//   For each of 3 slots take the next un-picked Centraal trip.
//   - CTR is "good"  → not cancelled AND delay < 10 min → use as-is.
//   - CTR is "bad"   → look for a clean HS substitute (see below); if none,
//                       fall through and show the bad CTR with its cancelled
//                       / late status visible to the user.
//   - nCtr == 0      → promote HS to primary (Trip Planner outage path),
//                       same clean-HS filter applies.
//
// Clean HS substitute = ALL of:
//   - not cancelled
//   - legCount <= 2  (rejects via-Rotterdam multi-transfer ghosts)
//   - departs within ±10 min of the bad CTR's planned departure
//   - departs >= now + 5 min  (reachable on foot)
//   - uniArr strictly earlier than every already-filled slot's uniArr
//     (no redundant arrivals — catching the same Breda→TBU sprinter as a
//      slot already shown adds zero info)
//
// No `note` text is written on substitution: the HS pill + thick outline in
// the card renderer is the origin signal. Common case it covers: IC delayed
// in, skips Centraal turnaround, leaves HS on time — visually obvious.
int pickDepartures(const Departure ctr[], int nCtr,
                   const Departure hs[],  int nHs,
                   Departure out[3]) {
  time_t now = time(nullptr);
  time_t prevPickTime = 0;

  bool ctrUsed[12] = {};
  bool hsUsed[12]  = {};

  // True iff `cand.uniArr` is strictly earlier than every already-filled
  // slot's uniArr. Empty `uniArr` (no realtime arrival known) is allowed —
  // can't dominance-check what we can't compare. strcmp on "HH:MM" orders
  // correctly within the dashboard's active window (night mode handles
  // midnight rollover by sleeping through it).
  auto arrivalIsUseful = [&](const Departure& cand, int filledSlots) -> bool {
    if (!cand.uniArr[0]) return true;
    for (int s = 0; s < filledSlots; s++) {
      if (!out[s].uniArr[0]) continue;
      if (strcmp(cand.uniArr, out[s].uniArr) >= 0) return false;
    }
    return true;
  };

  // nCtr == 0 promotion path: same 2-leg + reachability + dedup rules.
  if (nCtr == 0) {
    int picked = 0;
    for (int i = 0; i < nHs && i < 12 && picked < 3; i++) {
      if (hs[i].cancelled) continue;
      if (hs[i].legCount > 2) {
        DBG("  HS primary skip [legs="); DBG(hs[i].legCount);
        DBG("] "); DBGLN(hs[i].time);
        continue;
      }
      time_t t = parseISOToLocal(hs[i].plannedDepartureISO);
      if (t == 0 || t < now + 5 * 60) continue;
      if (picked > 0 &&
          strcmp(hs[i].plannedDepartureISO,
                 out[picked - 1].plannedDepartureISO) == 0) continue;
      if (!arrivalIsUseful(hs[i], picked)) {
        DBG("  HS primary skip [dominated] "); DBG(hs[i].time);
        DBG(" -> "); DBGLN(hs[i].uniArr);
        continue;
      }
      out[picked] = hs[i];
      out[picked].note[0] = 0;
      DBG("  slot "); DBG(picked); DBG(" = HS "); DBG(hs[i].time);
      DBG(" -> "); DBGLN(hs[i].uniArr);
      picked++;
    }
    return picked;
  }

  int picked = 0;
  while (picked < 3) {
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
      DBG("  slot "); DBG(picked); DBG(" = CTR "); DBG(ctr[ctrIdx].time);
      DBG(" -> "); DBGLN(ctr[ctrIdx].uniArr);
    } else {
      DBG("  CTR "); DBG(ctr[ctrIdx].time);
      DBG(ctr[ctrIdx].cancelled ? " cancelled" : " late");
      DBGLN(" — looking for clean HS substitute");

      int hsIdx = -1;
      for (int i = 0; i < nHs && i < 12; i++) {
        if (hsUsed[i] || hs[i].cancelled) continue;
        if (hs[i].legCount > 2) {
          DBG("    HS "); DBG(hs[i].time);
          DBG(" reject [legs="); DBG(hs[i].legCount); DBGLN("]");
          continue;
        }
        time_t t = parseISOToLocal(hs[i].plannedDepartureISO);
        if (t == 0) continue;
        long deltaMin = (long)((t - ctrTime) / 60);
        if (deltaMin < -10 || deltaMin > 10) {
          DBG("    HS "); DBG(hs[i].time);
          DBG(" reject [Δ="); DBG(deltaMin); DBGLN("m]");
          continue;
        }
        if (t < now + 5 * 60) {
          DBG("    HS "); DBG(hs[i].time); DBGLN(" reject [unreachable]");
          continue;
        }
        if (!arrivalIsUseful(hs[i], picked)) {
          DBG("    HS "); DBG(hs[i].time);
          DBG(" reject [dominated by earlier slot, uniArr=");
          DBG(hs[i].uniArr); DBGLN("]");
          continue;
        }
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
        out[picked].note[0] = 0;
        DBG("  slot "); DBG(picked); DBG(" = HS "); DBG(hs[hsIdx].time);
        DBG(" -> "); DBGLN(hs[hsIdx].uniArr);
      } else {
        out[picked] = ctr[ctrIdx];
        out[picked].note[0] = 0;
        DBG("  slot "); DBG(picked); DBG(" = CTR "); DBG(ctr[ctrIdx].time);
        DBGLN(" (no clean HS — showing disruption)");
      }
    }

    prevPickTime = ctrTime;
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

// Map a Buienradar single/double-letter iconcode to a WeatherCategory.
// Unknown codes fall through to OVERCAST.
WeatherCategory categorizeBuienradarIcon(const char* code) {
  if (!code || !code[0]) return OVERCAST;
  if (strcmp(code, "cc") == 0) return OVERCAST;  // only multi-char code

  switch (code[0]) {
    case 'a': case 'j':           return CLEAR;
    case 'b': case 'o': case 'r': return PARTLY_CLOUDY;
    case 'p': case 'c':           return OVERCAST;
    case 'd': case 'n':           return FOG;
    case 'f': case 'k':           return DRIZZLE;
    case 'h':                     return RAIN;
    case 'm': case 'q':           return RAIN_HEAVY;
    case 'g': case 's': case 'l': return THUNDERSTORM;
    case 'i': case 'u':
    case 'v': case 'w':           return SNOW;
    default:
      DBG("BR unknown iconcode: "); DBGLN(code);
      return OVERCAST;
  }
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