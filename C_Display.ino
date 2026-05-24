// ============================================================================
// DISPLAY FUNCTIONS
// ============================================================================
// Icon rendering and screen layout

// Custom fonts — Inter (see docs/redesign-plan.md "Typography").
#include "Fonts/Inter_Regular9pt7b.h"
#include "Fonts/Inter_Regular12pt7b.h"
#include "Fonts/Inter_Regular18pt7b.h"
#include "Fonts/Inter_Bold9pt7b.h"
#include "Fonts/Inter_Bold12pt7b.h"
#include "Fonts/Inter_Bold18pt7b.h"
#include "Fonts/Inter_Bold48pt7b.h"

// ============================================================================
// LAYOUT CONSTANTS
// ============================================================================
// Section Y coordinates are all absolute (pixel-accurate to design/mockup.html).
// Only the left/right gutters and the footer baseline survive as named
// constants — everything else is inlined per section.

const int MARGIN_LEFT  = 40;
const int MARGIN_RIGHT = 760;
const int FOOTER_Y     = 590;

// ============================================================================
// DRAWING HELPERS (Step 4 — primitives used by the new section renderers)
// ============================================================================

// --- Dashed lines ---------------------------------------------------------
// Adafruit GFX has no dash support. Walk the axis emitting dashPx-long runs
// separated by gapPx-long gaps. Used for the masthead horizon, week-strip
// divider, and rain-chart threshold reference lines.

void drawDashedH(int x1, int x2, int y, int dashPx = 2, int gapPx = 3) {
  if (x2 < x1) return;
  int x = x1;
  while (x <= x2) {
    int end = (x + dashPx - 1 < x2) ? (x + dashPx - 1) : x2;
    display.drawFastHLine(x, y, end - x + 1, BLACK);
    x = end + 1 + gapPx;
  }
}

void drawDashedV(int x, int y1, int y2, int dashPx = 2, int gapPx = 3) {
  if (y2 < y1) return;
  int y = y1;
  while (y <= y2) {
    int end = (y + dashPx - 1 < y2) ? (y + dashPx - 1) : y2;
    display.drawFastVLine(x, y, end - y + 1, BLACK);
    y = end + 1 + gapPx;
  }
}

// --- Bayer 50% checker fill ----------------------------------------------
// Pure black would be too heavy on e-ink for the rain-chart area fill and
// the disrupted-card delay strip. Classic single-pixel checker pattern.
// Stride of 2 per row halves the per-row work vs. a naive double loop.

void fillBayer50(int x, int y, int w, int h) {
  for (int j = 0; j < h; j++) {
    int startI = ((y + j) & 1) ^ (x & 1);  // start offset preserves global parity
    for (int i = startI; i < w; i += 2) {
      display.drawPixel(x + i, y + j, BLACK);
    }
  }
}

// --- Tiny right-arrow glyph ----------------------------------------------
// 7×3 px, vertically centered on baseline y. Used in the transfer line
// ("→ Uni HH:MM") because the U+2192 char isn't in our ASCII font header.
static void drawRightArrow(int x, int y) {
  display.drawFastHLine(x, y, 6, BLACK);
  display.drawPixel(x + 4, y - 1, BLACK);
  display.drawPixel(x + 5, y - 1, BLACK);
  display.drawPixel(x + 4, y + 1, BLACK);
  display.drawPixel(x + 5, y + 1, BLACK);
}

// --- Small-caps with per-char letter-spacing ------------------------------
// Mimics the SVG `letter-spacing: 0.22em` look from the mockup. Each char is
// uppercased then drawn at cursor; cursor advances by glyph width + a gap
// proportional to glyph width (so wide M and narrow I look evenly spaced).
// Caller is responsible for setFont and setTextColor before calling.
// `extraGapPx` adds a constant on top of the proportional gap.

// Per-char advance for the currently-set font. `getTextBounds` on a single
// char returns the drawn-pixel bbox width, NOT the font's xAdvance — for
// narrow glyphs like 'I' those differ a lot, so we'd squash neighbours.
// Trick: bbox("XX") − bbox("X") = xAdvance of "X" (the bbox of a two-char
// string includes the advance between the two glyphs, the one-char bbox
// does not). Works for any current font; no need to walk GFXglyph internals.
static int _charXAdvance(char c) {
  char one[2] = {c, 0};
  char two[3] = {c, c, 0};
  int16_t bx; int16_t by; uint16_t bw1, bw2, bh;
  display.getTextBounds(one, 0, 0, &bx, &by, &bw1, &bh);
  display.getTextBounds(two, 0, 0, &bx, &by, &bw2, &bh);
  int adv = (int)bw2 - (int)bw1;
  if (adv < 1) adv = (int)bw1;  // safety floor
  return adv;
}

void drawSmallCaps(int x, int y, const char* text, uint16_t color = BLACK, int extraGapPx = 1) {
  display.setTextColor(color);
  display.setCursor(x, y);
  int prevX = x;
  for (const char* p = text; *p; p++) {
    char c = *p;
    if (c >= 'a' && c <= 'z') c -= 32;
    if (c == ' ') {
      int cx = display.getCursorX();
      display.setCursor(cx + 5 + extraGapPx, y);
      prevX = cx + 5 + extraGapPx;
      continue;
    }
    display.print(c);
    int afterX = display.getCursorX();
    int adv = afterX - prevX;                       // actual xAdvance of this char
    int gap = (int)(0.22f * adv + 0.5f) + extraGapPx;
    display.setCursor(afterX + gap, y);
    prevX = afterX + gap;
  }
}

int smallCapsWidth(const char* text, int extraGapPx = 1) {
  int total = 0;
  int last = 0;
  for (const char* p = text; *p; p++) {
    char c = *p;
    if (c >= 'a' && c <= 'z') c -= 32;
    if (c == ' ') { total += 5 + extraGapPx; last = extraGapPx; continue; }
    int adv = _charXAdvance(c);
    int gap = (int)(0.22f * adv + 0.5f) + extraGapPx;
    total += adv + gap;
    last = gap;
  }
  return total - last;  // strip trailing gap
}

// --- Sun arc (masthead) ---------------------------------------------------
// Half-ellipse from (xLeft, baselineY) rising to height `rise` and back down
// to (xRight, baselineY). Parametric sampling at fine angular steps — for
// the masthead dimensions (rx≈70, ry=24) 0.005 rad gives sub-pixel coverage
// with no gaps. drawPixel writes straight to the frame buffer so the ~630
// samples cost <1 ms.

void drawSunArc(int xLeft, int xRight, int baselineY, int rise) {
  int cx = (xLeft + xRight) / 2;
  int rx = (xRight - xLeft) / 2;
  int ry = rise;
  if (rx <= 0 || ry <= 0) return;
  for (float t = 0.0f; t <= 1.0001f; t += 0.005f) {
    float angle = PI * t;
    int px = cx - (int)(rx * cosf(angle) + 0.5f);
    int py = baselineY - (int)(ry * sinf(angle) + 0.5f);
    display.drawPixel(px, py, BLACK);
  }
}

// Returns the (x, y) point on the arc at parametric t∈[0,1].
// t=0 is the left end (sunrise), t=1 the right end (sunset), t=0.5 the apex.
void sunDotForT(int xLeft, int xRight, int baselineY, int rise, float t,
                int& outX, int& outY) {
  if (t < 0.0f) t = 0.0f;
  if (t > 1.0f) t = 1.0f;
  // X is linear in time so the dot moves uniformly across the horizon
  // (a cosine parametrization compresses the last ~7% of time into the
  // last ~2% of horizontal space, making "1 h before sunset" look like
  // "already at the horizon"). Y is then derived from the half-circle
  // equation so the dot still lands on the drawn arc at the endpoints.
  float u = 2.0f * t - 1.0f;                                       // -1..+1
  outX = xLeft + (int)(t * (float)(xRight - xLeft) + 0.5f);
  outY = baselineY - (int)((float)rise * sqrtf(1.0f - u * u) + 0.5f);
}

// ============================================================================
// CURRENT WEATHER CODE CATEGORIZATION (for 128px icon)
// ============================================================================

WeatherCategory categorizeCurrentWeather(int code) {
  // WMO: 0 = clear, 1 = mainly clear, 2 = partly cloudy, 3 = overcast.
  // Treat "mainly clear" as clear — to a human it looks the same.
  if (code == 0 || code == 1) return CLEAR;
  if (code == 2) return PARTLY_CLOUDY;
  if (code == 3) return OVERCAST;
  if (code == 45 || code == 48) return FOG;
  if (code >= 51 && code <= 57) return DRIZZLE;
  if (code >= 61 && code <= 67) return RAIN;
  if (code >= 71 && code <= 77) return SNOW;
  if (code >= 80 && code <= 82) return RAIN_HEAVY;
  if (code == 85 || code == 86) return SNOW;
  if (code == 95 || code == 96 || code == 99) return THUNDERSTORM;
  return OVERCAST;
}

// ============================================================================
// ICON DRAWING FUNCTIONS
// ============================================================================

void drawWeatherIcon128(int x, int y, int code, bool isNight) {
  const uint8_t* bitmap;
  WeatherCategory category = categorizeCurrentWeather(code);
  
  switch (category) {
    case CLEAR:
      bitmap = isNight ? icon_moon_128 : icon_sun_128;
      break;
    case PARTLY_CLOUDY:
      bitmap = isNight ? icon_cloud_moon_128 : icon_cloud_sun_128;
      break;
    case OVERCAST:
      bitmap = icon_cloud_128;
      break;
    case FOG:
      bitmap = icon_fog_128;
      break;
    case DRIZZLE:
      bitmap = icon_rain_light_128;
      break;
    case RAIN:
      bitmap = icon_rain_128;
      break;
    case RAIN_HEAVY:
      bitmap = icon_rain_heavy_128;
      break;
    case SNOW:
      bitmap = icon_snow_128;
      break;
    case THUNDERSTORM:
      bitmap = icon_lightning_128;
      break;
    default:
      bitmap = icon_cloud_128;
      break;
  }
  
  display.drawBitmap(x, y, bitmap, 128, 128, WHITE, BLACK);
}

// 48×48 day-time forecast icons (week strip). Auto-downscaled from the
// 64×64 set by design/downscale_icons.py — hand-redraw any glyph that looks
// rough at this size (lightning and snowflake are the usual candidates).
void drawWeatherIcon48(int x, int y, WeatherCategory category, bool useSunnyVariant) {
  const uint8_t* bitmap;
  switch (category) {
    case CLEAR:          bitmap = icon_sun_48; break;
    case PARTLY_CLOUDY:  bitmap = icon_cloud_sun_48; break;
    case OVERCAST:       bitmap = icon_cloud_48; break;
    case FOG:            bitmap = icon_fog_48; break;
    case DRIZZLE:        bitmap = useSunnyVariant ? icon_sun_rain_48 : icon_rain_light_48; break;
    case RAIN:           bitmap = useSunnyVariant ? icon_sun_rain_48 : icon_rain_48; break;
    case RAIN_HEAVY:     bitmap = useSunnyVariant ? icon_sun_rain_48 : icon_rain_heavy_48; break;
    case SNOW:           bitmap = useSunnyVariant ? icon_sun_snow_48 : icon_snow_48; break;
    case THUNDERSTORM:   bitmap = icon_lightning_48; break;
    default:             bitmap = icon_cloud_48; break;
  }
  display.drawBitmap(x, y, bitmap, 48, 48, WHITE, BLACK);
}

void drawWeatherIcon64(int x, int y, WeatherCategory category, bool useSunnyVariant) {
  const uint8_t* bitmap;
  
  switch (category) {
    case CLEAR:
      bitmap = icon_sun_64;
      break;
      
    case PARTLY_CLOUDY:
      bitmap = icon_cloud_sun_64;
      break;
      
    case OVERCAST:
      bitmap = icon_cloud_64;
      break;
      
    case FOG:
      bitmap = icon_fog_64;
      break;
      
    case DRIZZLE:
      bitmap = useSunnyVariant ? icon_sun_rain_64 : icon_rain_light_64;
      break;
      
    case RAIN:
      bitmap = useSunnyVariant ? icon_sun_rain_64 : icon_rain_64;
      break;
      
    case RAIN_HEAVY:
      bitmap = useSunnyVariant ? icon_sun_rain_64 : icon_rain_heavy_64;
      break;
      
    case SNOW:
      bitmap = useSunnyVariant ? icon_sun_snow_64 : icon_snow_64;
      break;
      
    case THUNDERSTORM:
      bitmap = icon_lightning_64;
      break;
      
    default:
      bitmap = icon_cloud_64;
      break;
  }
  
  display.drawBitmap(x, y, bitmap, 64, 64, WHITE, BLACK);
}

void showError(const char* msg) {
  display.clearDisplay();
  display.setFont(&Inter_Regular18pt7b);
  display.setCursor(150, 300);
  display.print("Error: ");
  display.print(msg);
  display.setFont();
  display.display();
}

// ============================================================================
// SECTION RENDERERS
// ============================================================================

// Convert "HH:MM" to minutes-from-midnight, or -1 on bad input.
static int hhmmToMinutes(const char* hhmm) {
  if (!hhmm || strlen(hhmm) < 5) return -1;
  return (hhmm[0] - '0') * 600 + (hhmm[1] - '0') * 60
       + (hhmm[3] - '0') * 10  + (hhmm[4] - '0');
}

// Masthead per redesign-plan.md "Masthead (y=0–92)".
//   Left:  greeting (40, 48) Inter_Regular18pt7b; date (40, 72) Inter_Regular12pt7b
//   Right: sun arc + dot over a dashed horizon at y=58, sunrise/sunset times
//          centered below. Night branch swaps the arc for a moon glyph.
void drawHeader(struct tm &timeinfo, DayForecast forecast[], int forecastCount,
                bool forecastOk, bool isNight) {

  // --- Greeting ---
  const char* greeting;
  int h = timeinfo.tm_hour;
  if (isNight)        greeting = "Good evening";
  else if (h < 12)    greeting = "Good morning";
  else if (h < 18)    greeting = "Good afternoon";
  else                greeting = "Good evening";

  display.setFont(&Inter_Bold18pt7b);
  display.setTextColor(BLACK);
  display.setCursor(40, 48);
  display.print(greeting);

  // --- Date "Sun · 24 May 2026" ---
  // The middot glyph isn't in our ASCII-only font header, so draw a small
  // filled circle between the day name and the date proper.
  const char* days[]   = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
  const char* months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  char dateRight[24];
  snprintf(dateRight, sizeof(dateRight), "%02d %s %d",
           timeinfo.tm_mday, months[timeinfo.tm_mon],
           timeinfo.tm_year + 1900);

  display.setFont(&Inter_Regular9pt7b);
  display.setCursor(40, 72);
  display.print(days[timeinfo.tm_wday]);

  int16_t bx, by; uint16_t bw, bh;
  display.getTextBounds(days[timeinfo.tm_wday], 0, 0, &bx, &by, &bw, &bh);
  int afterDay = 40 + bw;
  display.fillCircle(afterDay + 6, 68, 2, BLACK);  // middot, vertically centered on x-height
  display.setCursor(afterDay + 12, 72);
  display.print(dateRight);

  // --- Right side: arc + dot + endpoint times (day or night) ---
  // Day:   sunrise → sunset arc with a filled sun dot.
  // Night: sunset → tomorrow's sunrise arc with a small crescent moon.
  // Both share the same arc geometry; only the dot shape and the t basis
  // differ. Falls back to a static moon glyph when forecast is unavailable.
  const int arcLeft   = 620;
  const int arcRight  = 760;
  const int horizonY  = 58;
  const int arcRise   = 24;

  const char* leftTime  = nullptr;
  const char* rightTime = nullptr;
  float t = -1.0f;  // -1 sentinel = "no valid arc data, fall back"

  if (forecastOk && forecastCount >= 1) {
    int nowMin = timeinfo.tm_hour * 60 + timeinfo.tm_min;
    if (!isNight) {
      int sr = hhmmToMinutes(forecast[0].sunrise);
      int ss = hhmmToMinutes(forecast[0].sunset);
      if (sr >= 0 && ss > sr) {
        t = (float)(nowMin - sr) / (float)(ss - sr);
        leftTime  = forecast[0].sunrise;
        rightTime = forecast[0].sunset;
      }
    } else {
      // Night arc crosses midnight: (now − sunset) over (24h − sunset + tomorrow_sunrise).
      // Tomorrow's sunrise comes from forecast[1]; if unavailable, today's sunrise is
      // close enough (adjacent days differ by ~1 min).
      int ss = hhmmToMinutes(forecast[0].sunset);
      int srTom = (forecastCount >= 2) ? hhmmToMinutes(forecast[1].sunrise) : -1;
      if (srTom < 0) srTom = hhmmToMinutes(forecast[0].sunrise);
      if (ss >= 0 && srTom >= 0) {
        int nightDur = (24 * 60 - ss) + srTom;
        int elapsed  = (nowMin >= ss) ? (nowMin - ss) : ((24 * 60 - ss) + nowMin);
        if (nightDur > 0) t = (float)elapsed / (float)nightDur;
        leftTime  = forecast[0].sunset;
        rightTime = (forecastCount >= 2 && forecast[1].sunrise[0])
                        ? forecast[1].sunrise : forecast[0].sunrise;
      }
    }
  }

  if (t >= 0.0f) {
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;

    drawDashedH(arcLeft, arcRight, horizonY, 2, 3);
    drawSunArc(arcLeft, arcRight, horizonY, arcRise);

    int dotX, dotY;
    sunDotForT(arcLeft, arcRight, horizonY, arcRise, t, dotX, dotY);
    if (isNight) {
      // Mini crescent: black disc with an off-center white disc bitten out.
      display.fillCircle(dotX, dotY, 5, BLACK);
      display.fillCircle(dotX - 2, dotY, 4, WHITE);
    } else {
      display.fillCircle(dotX, dotY, 4, BLACK);
    }

    display.setFont(&Inter_Regular9pt7b);
    auto centerPrint = [](int cx, int cy, const char* s) {
      if (!s || !s[0]) return;
      int16_t bx, by; uint16_t bw, bh;
      display.getTextBounds(s, 0, 0, &bx, &by, &bw, &bh);
      display.setCursor(cx - bw / 2, cy);
      display.print(s);
    };
    centerPrint(arcLeft,  78, leftTime);
    centerPrint(arcRight, 78, rightTime);
  } else if (isNight) {
    // Fallback only when night AND forecast unavailable: the original static
    // crescent at the panel center.
    display.fillCircle(690, 50, 14, BLACK);
    display.fillCircle(684, 50, 12, WHITE);
  }

  display.setFont();

  // Thick rule (2 px) below the masthead
  display.fillRect(40, 92, MARGIN_RIGHT - 40 + 1, 2, BLACK);
}

// --- Symbol primitives used by the metadata line and big temperature ---
// Degree ring: outer filled circle minus inner filled circle (1-bit "annulus").
static void drawDegreeRing(int cx, int cy, int outerR, int innerR) {
  display.fillCircle(cx, cy, outerR, BLACK);
  display.fillCircle(cx, cy, innerR, WHITE);
}
// Weather row left column per redesign-plan.md "Weather row (y=92–305) > Left".
//   Icon  (40, 125)          — 128×128, day/night variant
//   Temp  (195, 210) baseline — Inter_Bold48pt7b
//   Degree ring at the cap-top, right of the digits — r=9 outer, r=4 inner white
//   Wind row y=244: rotated 22 px arrow + "%d km/h %s"
//   Metadata y=272: "Feels %d° · Humid %d%% · UV %d" (Inter_Regular9pt7b)
void drawCurrentWeather(float temp, int weatherCode, float wind,
                        int windBearing, bool isNight) {
  // --- Icon ---
  drawWeatherIcon128(40, 125, weatherCode, isNight);

  // --- Big temperature digits ---
  char tempStr[8];
  snprintf(tempStr, sizeof(tempStr), "%d", (int)temp);

  display.setFont(&Inter_Bold48pt7b);
  display.setTextColor(BLACK);
  display.setCursor(195, 210);
  display.print(tempStr);

  // Place the degree ring at the cap-top of the digits (computed from the
  // text bbox so it tracks 1- vs 2-digit widths automatically).
  int16_t bx, by; uint16_t bw, bh;
  display.getTextBounds(tempStr, 195, 210, &bx, &by, &bw, &bh);
  int degX = bx + bw + 14;
  int degY = by + 8;  // a bit below the very top of the digits
  drawDegreeRing(degX, degY, 9, 4);

  // --- Wind row: small rotated arrow + bearing text ---
  // Arrow centered at (207, 244), 22 px long (R=11), rotated so the tip
  // points downwind (compass direction = bearing + 180°).
  {
    const int cx = 207, cy = 244, R = 11;
    float motionDeg = (float)windBearing + 180.0f;
    float a = (90.0f - motionDeg) * PI / 180.0f;
    float ca = cosf(a), sa = sinf(a);
    int tipX  = (int)(cx + R * ca + 0.5f);
    int tipY  = (int)(cy - R * sa + 0.5f);
    int tailX = (int)(cx - R * ca + 0.5f);
    int tailY = (int)(cy + R * sa + 0.5f);
    display.drawLine(tailX, tailY, tipX, tipY, BLACK);

    // Filled arrowhead — small triangle pointing along the arrow direction.
    // Back-of-arrowhead is 5 px behind the tip, barbs 3 px lateral.
    float bx_f = tipX - 5 * ca;
    float by_f = tipY + 5 * sa;
    float px = -sa, py = -ca;        // perpendicular in screen coords
    int b1X = (int)(bx_f + 3 * px + 0.5f);
    int b1Y = (int)(by_f + 3 * py + 0.5f);
    int b2X = (int)(bx_f - 3 * px + 0.5f);
    int b2Y = (int)(by_f - 3 * py + 0.5f);
    display.fillTriangle(tipX, tipY, b1X, b1Y, b2X, b2Y, BLACK);
  }

  display.setFont(&Inter_Regular9pt7b);
  char windStr[24];
  // "from X" matches the cardinalCompass convention (direction wind comes
  // from), so the text and the downwind-pointing arrow no longer contradict.
  snprintf(windStr, sizeof(windStr), "%d km/h from %s",
           (int)(wind + 0.5f), cardinalCompass(windBearing));
  display.setCursor(227, 249);
  display.print(windStr);

  display.setFont();
}

// Dry-state 24h temperature curve for the right panel.
// Shares axes with the rain chart so the panel structure stays consistent.
// `hourly[0]` is the current hour; `hourly[i]` is now + i hours.
// Sunset / sunrise vertical guides are drawn when the events fall inside
// the 24h window. Min / max points are marked with bold labels.
void drawTempCurve(const float hourly[], int n,
                   struct tm &timeinfo,
                   DayForecast forecast[], int forecastCount, bool forecastOk) {
  if (n < 2) return;

  const int x0 = 420, x1 = 750;
  const int yTop = 140, yBot = 260;
  const int chartW = x1 - x0;
  const int chartH = yBot - yTop;

  // Axes
  display.drawLine(x0, yTop, x0, yBot, BLACK);
  display.drawLine(x0, yBot, x1, yBot, BLACK);

  // Temp range with ±1°C padding
  float tMin = hourly[0], tMax = hourly[0];
  int minIdx = 0, maxIdx = 0;
  for (int i = 1; i < n; i++) {
    if (hourly[i] < tMin) { tMin = hourly[i]; minIdx = i; }
    if (hourly[i] > tMax) { tMax = hourly[i]; maxIdx = i; }
  }
  float tLo = tMin - 1.0f;
  float tHi = tMax + 1.0f;
  float tRange = tHi - tLo;
  if (tRange < 0.1f) tRange = 1.0f;

  auto xForIdx  = [&](int i)   { return x0 + (int)((float)i * chartW / (float)(n - 1) + 0.5f); };
  auto yForTemp = [&](float t) { return yBot - (int)((t - tLo) / tRange * chartH + 0.5f); };

  // --- Day/night vertical guides (sunset, sunrise) ---
  if (forecastOk && forecastCount >= 1) {
    int now = timeinfo.tm_hour * 60 + timeinfo.tm_min;
    int ss_today = hhmmToMinutes(forecast[0].sunset);
    int sr_today = hhmmToMinutes(forecast[0].sunrise);
    int sr_tom   = (forecastCount >= 2) ? hhmmToMinutes(forecast[1].sunrise) : sr_today;

    auto drawGuide = [&](float offsetH, const char* label) {
      if (offsetH < 0.5f || offsetH > (float)(n - 1) - 0.5f) return;
      int gx = x0 + (int)(offsetH * chartW / (float)(n - 1) + 0.5f);
      drawDashedV(gx, yTop - 4, yBot - 1, 2, 3);
      display.setFont(&Inter_Regular9pt7b);
      int16_t bx, by; uint16_t bw, bh;
      display.getTextBounds(label, 0, 0, &bx, &by, &bw, &bh);
      display.setCursor(gx - bw / 2, 132);
      display.print(label);
    };

    // Sunset: next one (today's if still ahead, else tomorrow's ≈ same time + 24h)
    if (ss_today >= 0) {
      float off = (ss_today > now)
                      ? (float)(ss_today - now) / 60.0f
                      : (float)(ss_today + 24 * 60 - now) / 60.0f;
      drawGuide(off, "sunset");
    }
    // Sunrise: next one
    int sr = (sr_today > now) ? sr_today : sr_tom;
    if (sr >= 0) {
      float off = (sr > now) ? (float)(sr - now) / 60.0f
                             : (float)(sr + 24 * 60 - now) / 60.0f;
      drawGuide(off, "sunrise");
    }
  }

  // --- Y-axis temp ticks every 5°C ---
  // Skipped if they would visually collide with a min/max marker (within
  // 10 px) so the chart doesn't get visually crowded near the extremes.
  {
    display.setFont(&Inter_Regular9pt7b);
    int firstTick = (int)ceilf(tLo / 5.0f) * 5;
    int lastTick  = (int)floorf(tHi / 5.0f) * 5;
    int yMinMark  = yForTemp(hourly[minIdx]);
    int yMaxMark  = yForTemp(hourly[maxIdx]);
    for (int v = firstTick; v <= lastTick; v += 5) {
      int gy = yForTemp((float)v);
      if (abs(gy - yMinMark) < 10 || abs(gy - yMaxMark) < 10) continue;
      display.drawFastHLine(x0 - 2, gy, 2, BLACK);
      char buf[6];
      snprintf(buf, sizeof(buf), "%d", v);
      int16_t bx, by; uint16_t bw, bh;
      display.getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
      display.setCursor(x0 - 5 - bw, gy + bh / 2);
      display.print(buf);
    }
    display.setFont();
  }

  // --- Curve ---
  for (int i = 1; i < n; i++) {
    display.drawLine(xForIdx(i - 1), yForTemp(hourly[i - 1]),
                     xForIdx(i),     yForTemp(hourly[i]),     BLACK);
  }

  // --- Min / max markers with bold labels ---
  auto drawTempMarker = [&](int idx, int yOffset) {
    int px = xForIdx(idx);
    int py = yForTemp(hourly[idx]);
    display.fillCircle(px, py, 3, BLACK);

    display.setFont(&Inter_Bold9pt7b);
    display.setTextColor(BLACK);
    char buf[8];
    snprintf(buf, sizeof(buf), "%d", (int)(hourly[idx] + (hourly[idx] >= 0 ? 0.5f : -0.5f)));
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
    int textLeft = px - bw / 2 - 3;  // shift left to make room for degree ring
    int textY    = py + yOffset;
    display.setCursor(textLeft, textY);
    display.print(buf);
    drawDegreeRing(textLeft + bw + 4, textY - bh + 3, 2, 1);
  };
  // Both labels above their dots — placing min below collides with the
  // x-axis tick row at y=276 since min is by definition the chart's lowest y.
  drawTempMarker(maxIdx, -8);
  drawTempMarker(minIdx, -8);

  // --- X-axis tick labels ---
  display.setFont(&Inter_Regular9pt7b);
  const char* ticks[] = {"now", "+6h", "+12h", "+18h"};
  const int   hours[] = {0,     6,      12,     18};
  for (int i = 0; i < 4; i++) {
    if (hours[i] >= n) continue;
    int gx = xForIdx(hours[i]);
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(ticks[i], 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(gx - bw / 2, 276);
    display.print(ticks[i]);
  }

  display.setFont();
}

void drawRainChart(float rainData[], char timeLabels[][20], int rainCount) {
  const int x0 = 420, x1 = 750;
  const int yTop = 140, yBot = 260;
  const int chartH = yBot - yTop;

  // Precipitation thresholds (mm) — fixed semantic values.
  const float heavyThreshold  = 2.5f;
  const float mediumThreshold = 1.0f;
  const float lightThreshold  = 0.1f;
  float maxScale = 3.0f;

  bool hasRain = false;
  float maxRain = 0;
  for (int i = 0; i < rainCount; i++) {
    if (rainData[i] > 0.05f) hasRain = true;
    if (rainData[i] > maxRain) maxRain = rainData[i];
  }
  if (maxRain > maxScale) maxScale = maxRain + 0.5f;

  // Axes
  display.drawLine(x0, yTop, x0, yBot, BLACK);
  display.drawLine(x0, yBot, x1, yBot, BLACK);

  if (!hasRain) {
    display.setFont(&Inter_Regular9pt7b);
    const char* msg = "No rain expected";
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(msg, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(x0 + ((x1 - x0) - bw) / 2, yTop + chartH / 2);
    display.print(msg);
    display.setFont();
    return;
  }

  // 12 points spaced 30 px across the 330 px wide chart.
  const int n = min(12, rainCount);
  auto xForI    = [&](int i)     { return x0 + i * 30; };
  auto yForRain = [&](float mm)  {
    int h = (int)((mm / maxScale) * chartH + 0.5f);
    if (h > chartH) h = chartH;
    if (h < 0) h = 0;
    return yBot - h;
  };

  // --- Bayer area fill ---
  // For each column between points i-1 and i, interpolate the curve y and
  // fill from there down to the x-axis with the 50% checker pattern.
  for (int i = 1; i < n; i++) {
    int px = xForI(i - 1), py = yForRain(rainData[i - 1]);
    int cx = xForI(i),     cy = yForRain(rainData[i]);
    for (int fx = px; fx <= cx; fx++) {
      float t = (cx > px) ? (float)(fx - px) / (float)(cx - px) : 0.0f;
      int fy = py + (int)(t * (cy - py) + 0.5f);
      int h  = (yBot - 1) - fy;
      if (h > 0) fillBayer50(fx, fy, 1, h);
    }
  }

  // --- Top stroke (solid line connecting peaks) ---
  for (int i = 1; i < n; i++) {
    display.drawLine(xForI(i - 1), yForRain(rainData[i - 1]),
                     xForI(i),     yForRain(rainData[i]), BLACK);
  }

  // --- Threshold reference lines + INSIDE white-plate labels (bug fix) ---
  // Old code drew labels at x > x1 — they fell off the right edge of the
  // panel. Now labels live inside the chart on a small white plate so they
  // stay on-screen at any threshold y.
  auto drawThreshold = [&](float mm, const char* label) {
    int y = yForRain(mm);
    // Skip only if literally off-chart. The "Light" line lands just above
    // the x-axis (0.1 mm) and we still want its plate to show.
    if (y < yTop || y > yBot - 1) return;
    drawDashedH(x0 + 4, x1 - 2, y, 2, 3);

    display.setFont(&Inter_Regular9pt7b);
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(label, 0, 0, &bx, &by, &bw, &bh);
    int plateW = bw + 6;
    int plateX = (x1 - 2) - plateW;
    int plateY = y - bh / 2 - 2;
    display.fillRect(plateX, plateY, plateW + 1, bh + 4, WHITE);
    display.setTextColor(BLACK);
    display.setCursor((x1 - 2) - bw - 2, y + bh / 2 - 1);
    display.print(label);
  };
  drawThreshold(heavyThreshold,  "Heavy");
  drawThreshold(mediumThreshold, "Medium");
  drawThreshold(lightThreshold,  "Light");

  // --- Hour ticks on the x-axis (only at minute == 0) ---
  display.setFont(&Inter_Regular9pt7b);
  for (int i = 0; i < n; i++) {
    if (strlen(timeLabels[i]) < 16) continue;
    char buf[3] = {};
    strncpy(buf, timeLabels[i] + 14, 2); int minute = atoi(buf);
    if (minute != 0) continue;
    strncpy(buf, timeLabels[i] + 11, 2); int hour = atoi(buf);

    int x = xForI(i);
    display.drawLine(x, yBot, x, yBot + 4, BLACK);

    char timeStr[6];
    snprintf(timeStr, sizeof(timeStr), "%02d:00", hour);
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(timeStr, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(x - bw / 2, 276);
    display.print(timeStr);
  }

  display.setFont();
}

// Week strip per redesign-plan.md "Week strip (y=305–445)".
// 7 cells × 102 px starting at x=40 (so the strip spans x=40..754).
// Each cell is centered on its midpoint cMid = cellX + 51.
//   - Day name "Mon" at (cMid, 340) in bold
//   - 64×64 weather icon centered at (cMid − 32, 350) — Step 10 swaps for 48×48
//   - Range bar at y=418 from cMid−40 to cMid+40 against shared scale [-2°, +25°]
//   - Bold max label above right dot at y=410, regular min label below left dot at y=436
void drawWeekForecast(DayForecast forecast[], int forecastCount) {
  const int CELL_W   = 102;
  const int X0       = 40;
  const float TS_LO  = -2.0f;   // shared range-bar scale
  const float TS_HI  = 25.0f;
  const float TS_RNG = TS_HI - TS_LO;

  for (int i = 0; i < min(7, forecastCount); i++) {
    int cellX = X0 + i * CELL_W;
    int cMid  = cellX + CELL_W / 2;  // 51

    // --- Day name ---
    display.setFont(&Inter_Bold9pt7b);
    display.setTextColor(BLACK);
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(forecast[i].dayName, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(cMid - bw / 2, 340);
    display.print(forecast[i].dayName);

    // --- Icon: 48×48 centered on cMid ---
    drawWeatherIcon48(cMid - 24, 350,
                      forecast[i].category,
                      forecast[i].useSunnyVariant);

    // --- Range bar ---
    const int barY  = 418;
    const int barL  = cMid - 40;
    const int barR  = cMid + 40;
    const int barW  = barR - barL;

    // Background: dashed thin line spanning the full bar range
    drawDashedH(barL, barR, barY, 2, 3);

    // Filled segment between min and max temps on the shared scale
    int tMin = forecast[i].tempMin;
    int tMax = forecast[i].tempMax;
    auto xForT = [&](int t) {
      float clipped = t;
      if (clipped < TS_LO) clipped = TS_LO;
      if (clipped > TS_HI) clipped = TS_HI;
      return barL + (int)(((clipped - TS_LO) / TS_RNG) * barW + 0.5f);
    };
    int xMin = xForT(tMin);
    int xMax = xForT(tMax);
    if (xMax < xMin) { int t = xMin; xMin = xMax; xMax = t; }
    display.drawFastHLine(xMin, barY,     xMax - xMin + 1, BLACK);
    display.drawFastHLine(xMin, barY - 1, xMax - xMin + 1, BLACK);  // 2px thick segment
    display.fillCircle(xMin, barY, 3, BLACK);
    display.fillCircle(xMax, barY, 3, BLACK);

    // --- Max label (bold) above right dot ---
    char buf[6];
    snprintf(buf, sizeof(buf), "%d", tMax);
    display.setFont(&Inter_Bold9pt7b);
    display.getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(xMax - bw / 2, 410);
    display.print(buf);

    // --- Min label (regular) below left dot ---
    snprintf(buf, sizeof(buf), "%d", tMin);
    display.setFont(&Inter_Regular9pt7b);
    display.getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(xMin - bw / 2, 436);
    display.print(buf);
  }

  display.setFont();
}

// New departures cards per redesign-plan.md "Departures (y=455–590)".
// 3 cards, 220 × 80 each, starting at (40, 485), 20 px gap → x = 40 + i*240.
// Centraal cards: thin 1 px outline + small-caps origin label top-left.
// HS cards (substituted by the picker when Centraal is bad): 2 px outline +
// filled black pill with inverted "DH HS" + Centraal disruption note next to it.
void drawTrains(const Departure d[], int n) {
  if (n <= 0) {
    display.setFont(&Inter_Regular12pt7b);
    display.setCursor(40, 510);
    display.print("Train data unavailable");
    display.setFont();
    return;
  }

  const int CARD_W = 220, CARD_H = 80;
  const int CARD_Y = 485;

  for (int i = 0; i < min(3, n); i++) {
    const Departure& dep = d[i];
    int cx = 40 + i * 240;
    int cy = CARD_Y;

    // --- Outline (HS thicker than CTR) ---
    display.drawRect(cx, cy, CARD_W, CARD_H, BLACK);
    if (dep.origin == ORIGIN_HS) {
      display.drawRect(cx + 1, cy + 1, CARD_W - 2, CARD_H - 2, BLACK);
    }

    // --- Origin row ---
    // CTR: plain mixed-case "DH Centraal" so it reads as card metadata, not
    // as another small-caps section header. HS keeps tracked small-caps
    // because the inverted pill is a graphic element.
    if (dep.origin == ORIGIN_CTR) {
      display.setFont(&Inter_Regular9pt7b);
      display.setTextColor(BLACK);
      display.setCursor(cx + 14, cy + 18);
      display.print("DH Centraal");
    } else {
      // HS: filled black pill with inverted small-caps "DH HS"
      display.fillRect(cx + 4, cy + 4, 64, 18, BLACK);
      display.setFont(&Inter_Regular9pt7b);
      int pillCenter = cx + 4 + 64 / 2;
      int textW = smallCapsWidth("DH HS", 0);
      drawSmallCaps(pillCenter - textW / 2, cy + 18, "DH HS", WHITE, 0);

      if (dep.note[0]) {
        display.setTextColor(BLACK);
        display.setCursor(cx + 74, cy + 18);
        display.print(dep.note);
      }
    }

    // --- Time (bold) ---
    display.setFont(&Inter_Bold12pt7b);
    display.setTextColor(BLACK);
    display.setCursor(cx + 14, cy + 42);
    display.print(dep.time);

    // --- Inline status ---
    char statusBuf[20];
    if (dep.cancelled) {
      strncpy(statusBuf, "cancelled", sizeof(statusBuf));
    } else if (dep.delay[0]) {
      snprintf(statusBuf, sizeof(statusBuf), "%s late", dep.delay);
    } else {
      strncpy(statusBuf, "on time", sizeof(statusBuf));
    }
    statusBuf[sizeof(statusBuf) - 1] = 0;
    display.setFont(&Inter_Regular9pt7b);
    display.setCursor(cx + 90, cy + 42);
    display.print(statusBuf);

    // --- Track, bold, right-aligned ---
    display.setFont(&Inter_Bold9pt7b);
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(dep.track, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(cx + CARD_W - 14 - bw, cy + 42);
    display.print(dep.track);

    // --- Transfer line ---
    bool tCancelled = (dep.transfer == TRANSFER_CANCELLED);
    bool tLate      = (dep.transfer == TRANSFER_LATE);

    int tlX = cx + 14;
    int tlY = cy + 68;
    drawRightArrow(tlX, tlY - 4);
    int textX = tlX + 10;

    if (tCancelled) {
      display.setFont(&Inter_Bold9pt7b);
      display.setCursor(textX, tlY);
      display.print("Uni ");
      // small middot before the cancelled phrase
      int cxNow = display.getCursorX();
      display.fillCircle(cxNow + 3, tlY - 4, 1, BLACK);
      display.setCursor(cxNow + 9, tlY);
      display.print("transfer cancelled");
    } else {
      display.setFont(&Inter_Regular9pt7b);
      display.setCursor(textX, tlY);
      display.print("Uni ");
      display.print(dep.uniArr);
      if (tLate) {
        int cxNow = display.getCursorX();
        display.fillCircle(cxNow + 4, tlY - 4, 1, BLACK);
        display.setFont(&Inter_Bold9pt7b);
        display.setCursor(cxNow + 10, tlY);
        display.print("transfer late");
      }
    }
  }

  display.setFont();
}

void drawFooter() {
  // Left side: "Updated HH:MM" from current local time
  struct tm timeinfo;
  if (getLocalTime(&timeinfo)) {
    char buf[24];
    sprintf(buf, "Updated %02d:%02d", timeinfo.tm_hour, timeinfo.tm_min);
    display.setFont(&Inter_Regular9pt7b);
    display.setCursor(MARGIN_LEFT, FOOTER_Y);
    display.print(buf);
    display.setFont();
  }

  // Right side: battery icon only (no percentage text)
  float batteryV = display.readBattery();
  int pct = (batteryV > 0)
    ? constrain((int)((batteryV - 3.5f) / (4.2f - 3.5f) * 100.0f), 0, 100)
    : -1;  // -1 = no reading (USB power / no battery)

  // Battery icon: body 28×14px + 3px nub on right, right-aligned to MARGIN_RIGHT
  const int iconRight = MARGIN_RIGHT;
  const int iconTop   = FOOTER_Y - 13;
  const int bodyW = 28, bodyH = 14;
  const int nubW = 3,   nubH = 6;
  const int bodyX = iconRight - nubW - bodyW;

  display.drawRect(bodyX, iconTop, bodyW, bodyH, BLACK);
  display.fillRect(iconRight - nubW, iconTop + (bodyH - nubH) / 2, nubW, nubH, BLACK);
  if (pct > 0) {
    int fillW = (int)((pct / 100.0f) * (bodyW - 4));
    display.fillRect(bodyX + 2, iconTop + 2, fillW, bodyH - 4, BLACK);
  }
}

// ============================================================================
// MAIN DISPLAY UPDATE
// ============================================================================

void updateDisplay(
    float temp, float wind, int currentWeatherCode, bool weatherOk,
    WeatherExtras &extras,
    float rainData[], char timeLabels[][20], int rainCount, bool rainOk,
    DayForecast forecast[], int forecastCount, bool forecastOk,
    Departure departures[], int departureCount
  ) {
  
  display.clearDisplay();
  display.setRotation(0);

  // Get current time for header and day/night detection
  struct tm timeinfo;
  bool hasTime = getLocalTime(&timeinfo);

  // Night = before today's sunrise or after today's sunset. Uses the actual
  // sun times from the forecast so it tracks the season. Falls back to a
  // wide fixed window only when the forecast hasn't arrived.
  bool isNight = false;
  if (hasTime) {
    int nowMin = timeinfo.tm_hour * 60 + timeinfo.tm_min;
    int sr = (forecastOk && forecastCount >= 1) ? hhmmToMinutes(forecast[0].sunrise) : -1;
    int ss = (forecastOk && forecastCount >= 1) ? hhmmToMinutes(forecast[0].sunset)  : -1;
    if (sr >= 0 && ss > sr) {
      isNight = (nowMin < sr) || (nowMin >= ss);
    } else {
      isNight = (timeinfo.tm_hour >= 20 || timeinfo.tm_hour < 6);
    }
  }

  // Header
  if (hasTime) {
    drawHeader(timeinfo, forecast, forecastCount, forecastOk, isNight);
  }

  // Section 1: Current Weather (left) + rotating right panel
  display.setFont(&Inter_Regular9pt7b);
  drawSmallCaps(40, 112, "WEATHER");

  if (weatherOk && hasTime) {
    drawCurrentWeather(temp, currentWeatherCode, wind,
                       extras.windDirection, isNight);
  } else {
    display.setFont(&Inter_Regular12pt7b);
    display.setCursor(MARGIN_LEFT, 200);
    display.print("Weather data unavailable");
    display.setFont();
  }

  // Right panel: rain chart when rain ≥0.1 mm in the next 3 h, else temp curve.
  bool rainComing = false;
  if (rainOk) {
    for (int i = 0; i < rainCount; i++) {
      if (rainData[i] >= 0.1f) { rainComing = true; break; }
    }
  }
  if (rainComing) {
    display.setFont(&Inter_Regular9pt7b);
    drawSmallCaps(420, 112, "RAIN COMING");
    drawRainChart(rainData, timeLabels, rainCount);
  } else if (hasTime && extras.hourlyCount >= 2) {
    display.setFont(&Inter_Regular9pt7b);
    drawSmallCaps(420, 112, "NEXT HOURS DRY");
    drawTempCurve(extras.hourlyTemp, extras.hourlyCount,
                  timeinfo, forecast, forecastCount, forecastOk);
  } else {
    // Neither rain nor hourly outlook available — explicit fallback so the
    // right panel doesn't read as a layout bug.
    display.setFont(&Inter_Regular9pt7b);
    drawSmallCaps(420, 112, "OUTLOOK");
    display.setFont(&Inter_Regular12pt7b);
    const char* msg = "Outlook unavailable";
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(msg, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(420 + (330 - bw) / 2, 200);
    display.print(msg);
    display.setFont();
  }
  
  // Dotted divider before the week strip (per redesign-plan masthead/week split)
  drawDashedH(MARGIN_LEFT, MARGIN_RIGHT, 305, 2, 3);

  // Section 2: Week strip
  display.setFont(&Inter_Regular9pt7b);
  drawSmallCaps(40, 324, "WEEK");
  if (forecastOk) {
    drawWeekForecast(forecast, forecastCount);
  }

  // Dotted divider before the departures strip
  drawDashedH(MARGIN_LEFT, MARGIN_RIGHT, 455, 2, 3);

  // Section 3 label: "DEPARTURES · TO BREDA" with a drawn middot (the
  // U+00B7 char isn't in our ASCII font header).
  display.setFont(&Inter_Regular9pt7b);
  {
    int leftW = smallCapsWidth("DEPARTURES");
    drawSmallCaps(40, 474, "DEPARTURES");
    int dotX = 40 + leftW + 8;
    display.fillCircle(dotX, 470, 1, BLACK);
    drawSmallCaps(dotX + 6, 474, "TO BREDA");
  }

  // Section 3: Trains (new Departure-based cards)
  drawTrains(departures, departureCount);

  // Footer
  drawFooter();

  // E-ink refresh strategy:
  //   - Cold boot (wakeCounter == 0): full refresh, no ghosting baseline.
  //   - Every Nth wake: full refresh to clear accumulated ghosting.
  //   - Otherwise: partial update — much faster and lower energy.
  // Inkplate library tracks the previous framebuffer internally for partials.
  bool fullRefresh = (wakeCounter == 0) || (wakeCounter % FULL_REFRESH_EVERY == 0);
  if (fullRefresh) {
    DBGLN("Full refresh");
    display.display();
  } else {
    DBGLN("Partial refresh");
    display.partialUpdate();
  }
}