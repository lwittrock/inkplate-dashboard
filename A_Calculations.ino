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