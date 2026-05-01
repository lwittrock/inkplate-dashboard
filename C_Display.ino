// ============================================================================
// DISPLAY FUNCTIONS
// ============================================================================
// Icon rendering and screen layout

// Custom fonts
#include "Fonts/FreeSansBold48pt7b.h"
#include "Fonts/FreeSans18pt7b.h"
#include "Fonts/FreeSans12pt7b.h"
#include "Fonts/FreeSans9pt7b.h"

// ============================================================================
// LAYOUT CONSTANTS - ADJUST THESE TO CHANGE POSITIONS
// ============================================================================

// Screen margins
const int MARGIN_LEFT = 40;
const int MARGIN_RIGHT = 760;
const int MARGIN_TOP = 40;

// Section heights (how tall each section is)
const int HEADER_HEIGHT = 50;
const int WEATHER_HEIGHT = 170;
const int FORECAST_HEIGHT = 170;
const int TRAINS_HEIGHT = 170;

// Gaps between sections
const int GAP_AFTER_HEADER = 15;
const int GAP_AFTER_WEATHER = 10;
const int GAP_AFTER_FORECAST = 10;

// Calculated Y positions (don't edit these - they auto-calculate!)
const int HEADER_Y = MARGIN_TOP + 25;
const int HEADER_DIVIDER_Y = MARGIN_TOP + HEADER_HEIGHT;

const int WEATHER_Y = HEADER_DIVIDER_Y + GAP_AFTER_HEADER;
const int WEATHER_DIVIDER_Y = WEATHER_Y + WEATHER_HEIGHT;

const int FORECAST_Y = WEATHER_DIVIDER_Y + GAP_AFTER_WEATHER;
const int FORECAST_DIVIDER_Y = FORECAST_Y + FORECAST_HEIGHT;

const int TRAINS_Y = FORECAST_DIVIDER_Y + GAP_AFTER_FORECAST;

const int FOOTER_Y = 590;  // Fixed at bottom

// Rain chart positioning (relative to weather section)
const int RAIN_CHART_X = 420;
const int RAIN_CHART_Y = WEATHER_Y;
const int RAIN_Y_AXIS_X = RAIN_CHART_X - 15;  // Y-axis slightly left of chart

// Week forecast spacing
const int FORECAST_DAY_SPACING = 105;

// Train display spacing
const int TRAIN_SPACING = 120;

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

void drawTrainIcon(int x, int y) {
  display.drawBitmap(x, y, icon_train_32, 32, 32, BLACK);
}

void drawRaindrop(int x, int y) {
  display.drawBitmap(x, y, icon_raindrop_16, 16, 16, WHITE, BLACK);
}

void showError(const char* msg) {
  display.clearDisplay();
  display.setFont(&FreeSans18pt7b);
  display.setCursor(150, 300);
  display.print("Error: ");
  display.print(msg);
  display.setFont();
  display.display();
}

const char* getWindDescription(float windSpeed) {
  // Beaufort scale approximation (km/h)
  // Wind speed from API is in km/h
  if (windSpeed < 2) return "Calm";
  if (windSpeed < 12) return "Light wind";
  if (windSpeed < 29) return "Moderate wind";
  if (windSpeed < 39) return "Fresh wind";
  if (windSpeed < 50) return "Strong wind";
  if (windSpeed < 62) return "Very strong";
  if (windSpeed < 75) return "Storm";
  return "Severe storm";
}

// ============================================================================
// SECTION RENDERERS
// ============================================================================

void drawHeader(struct tm &timeinfo, DayForecast forecast[], int forecastCount, bool forecastOk) {
  display.setFont(&FreeSans18pt7b);
  display.setCursor(MARGIN_LEFT, HEADER_Y);

  const char* months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  const char* days[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};

  char dateStr[60];
  sprintf(dateStr, "%s %02d %s %d",
          days[timeinfo.tm_wday],
          timeinfo.tm_mday,
          months[timeinfo.tm_mon],
          timeinfo.tm_year + 1900);

  display.print(dateStr);

  // Right-aligned: sunset before it happens, otherwise tomorrow's sunrise.
  if (forecastOk && forecastCount >= 1) {
    int nowMin = timeinfo.tm_hour * 60 + timeinfo.tm_min;
    char buf[24] = {0};

    auto toMinutes = [](const char* hhmm) -> int {
      if (!hhmm || strlen(hhmm) < 5) return -1;
      return (hhmm[0] - '0') * 600 + (hhmm[1] - '0') * 60
           + (hhmm[3] - '0') * 10  + (hhmm[4] - '0');
    };

    int sunsetMin = toMinutes(forecast[0].sunset);
    if (sunsetMin >= 0 && nowMin < sunsetMin) {
      snprintf(buf, sizeof(buf), "Sunset %s", forecast[0].sunset);
    } else if (forecastCount >= 2 && forecast[1].sunrise[0]) {
      snprintf(buf, sizeof(buf), "Sunrise %s", forecast[1].sunrise);
    } else if (forecast[0].sunrise[0]) {
      snprintf(buf, sizeof(buf), "Sunrise %s", forecast[0].sunrise);
    }

    if (buf[0]) {
      int16_t bx, by; uint16_t bw, bh;
      display.getTextBounds(buf, 0, 0, &bx, &by, &bw, &bh);
      display.setCursor(MARGIN_RIGHT - bw, HEADER_Y);
      display.print(buf);
    }
  }

  display.setFont();

  // Divider line after header
  display.drawLine(MARGIN_LEFT, HEADER_DIVIDER_Y, MARGIN_RIGHT, HEADER_DIVIDER_Y, BLACK);
}

void drawCurrentWeather(float temp, int weatherCode, float wind, bool isNight) {
  drawWeatherIcon128(MARGIN_LEFT, WEATHER_Y, weatherCode, isNight);
  
  // Temperature
  display.setFont(&FreeSansBold48pt7b);
  display.setCursor(185, WEATHER_Y + 100);
  display.print((int)temp);

  // Degree symbol: clean filled ring (outer black + inner white), positioned
  // top-right of the temperature digits.
  int tempWidth = ((int)temp >= 10 || (int)temp <= -1) ? 120 : 65;
  int degX = 185 + tempWidth + 8;
  int degY = WEATHER_Y + 40;
  display.fillCircle(degX, degY, 8, BLACK);
  display.fillCircle(degX, degY, 4, WHITE);
  display.setFont();

  // Wind description below icon
  display.setFont(&FreeSans12pt7b);
  display.setCursor(MARGIN_LEFT, WEATHER_Y + 145);
  display.print(getWindDescription(wind));
  display.setFont();
}

void drawRainChart(float rainData[], char timeLabels[][20], int rainCount) {
  // Chart dimensions
  int chartHeight = 120;
  int chartWidth = 330;
  int chartBottom = RAIN_CHART_Y + chartHeight;
  
  // Precipitation thresholds (mm)
  float heavyThreshold = 2.5;
  float mediumThreshold = 1.0;
  float lightThreshold = 0.1;
  float maxScale = 3.0;
  
  // Check if we have rain
  bool hasRain = false;
  float maxRain = 0;
  for (int i = 0; i < rainCount; i++) {
    if (rainData[i] > 0.05) hasRain = true;
    if (rainData[i] > maxRain) maxRain = rainData[i];
  }
  
  if (maxRain > maxScale) maxScale = maxRain + 0.5;
  
  // Draw axes
  display.drawLine(RAIN_Y_AXIS_X, RAIN_CHART_Y, RAIN_Y_AXIS_X, chartBottom, BLACK);
  display.drawLine(RAIN_Y_AXIS_X, chartBottom, RAIN_CHART_X + chartWidth, chartBottom, BLACK);
  
  if (!hasRain) {
    display.setFont(&FreeSans12pt7b);
    display.setCursor(RAIN_CHART_X + 80, RAIN_CHART_Y + 60);
    display.print("No rain expected");
    display.setFont();
  } else {
    // Draw line chart with fill
    int barSpacing = 28;
    
    for (int i = 0; i < min(12, rainCount); i++) {
      int x = RAIN_CHART_X + i * barSpacing;
      int h = (rainData[i] / maxScale) * chartHeight;
      int y = chartBottom - h;
      
      if (i == 0) {
        display.drawLine(x, chartBottom, x, y, BLACK);
      } else {
        int prevX = RAIN_CHART_X + (i - 1) * barSpacing;
        int prevH = (rainData[i - 1] / maxScale) * chartHeight;
        int prevY = chartBottom - prevH;
        
        display.drawLine(prevX, prevY, x, y, BLACK);
        
        for (int fillX = prevX; fillX <= x; fillX++) {
          float t = (float)(fillX - prevX) / (x - prevX);
          int fillY = prevY + t * (y - prevY);
          display.drawLine(fillX, chartBottom, fillX, fillY, BLACK);
        }
      }
      
      if (i == min(12, rainCount) - 1) {
        display.drawLine(x, chartBottom, x, y, BLACK);
      }
    }
    
    // Draw dashed reference lines in WHITE
    auto drawDashedLine = [&](int y, const char* label) {
      for (int x = RAIN_Y_AXIS_X; x < RAIN_CHART_X + chartWidth; x += 8) {
        display.drawPixel(x, y, WHITE);
        display.drawPixel(x + 1, y, WHITE);
        display.drawPixel(x + 2, y, WHITE);
      }
      
      display.setFont(&FreeSans9pt7b);
      display.setCursor(RAIN_CHART_X + chartWidth + 10, y + 5);
      display.print(label);
      display.setFont();
    };
    
    int heavyY = chartBottom - (heavyThreshold / maxScale) * chartHeight;
    int mediumY = chartBottom - (mediumThreshold / maxScale) * chartHeight;
    int lightY = chartBottom - (lightThreshold / maxScale) * chartHeight;
    
    if (heavyY > RAIN_CHART_Y && heavyY < chartBottom) drawDashedLine(heavyY, "Heavy");
    if (mediumY > RAIN_CHART_Y && mediumY < chartBottom) drawDashedLine(mediumY, "Medium");
    if (lightY > RAIN_CHART_Y && lightY < chartBottom) drawDashedLine(lightY, "Light");
  }
  
  // X-axis time labels using actual API timestamps
  display.setFont(&FreeSans9pt7b);
  int barSpacing = 28;
  
  for (int i = 0; i < min(12, rainCount); i++) {
    if (strlen(timeLabels[i]) < 16) continue;

    // Extract time from ISO8601: "2026-02-01T17:30"
    char buf[3] = {};
    strncpy(buf, timeLabels[i] + 11, 2); int hour   = atoi(buf);
    strncpy(buf, timeLabels[i] + 14, 2); int minute  = atoi(buf);
    
    int x = RAIN_CHART_X + i * barSpacing;
    
    // Only label on the hour (minute == 00)
    if (minute == 0) {
      // Tick mark
      display.drawLine(x, chartBottom, x, chartBottom + 5, BLACK);
      
      // Time label
      char timeStr[6];
      sprintf(timeStr, "%02d:00", hour);
      display.setCursor(x - 18, chartBottom + 20);
      display.print(timeStr);
    }
  }
  
  display.setFont();
}

void drawWeekForecast(DayForecast forecast[], int forecastCount) {
  for (int i = 0; i < min(7, forecastCount); i++) {
    int x = MARGIN_LEFT + i * FORECAST_DAY_SPACING;
    int baseY = FORECAST_Y;
    
    // Day name
    display.setFont(&FreeSans12pt7b);
    display.setCursor(x + 8, baseY + 25);
    display.print(forecast[i].dayName);
    display.setFont();
    
    // Weather icon
    drawWeatherIcon64(x, baseY + 30, 
                     forecast[i].category, 
                     forecast[i].useSunnyVariant);
    
    // Temperature: "12 / 5" format, right-aligned to icon's right edge
    display.setFont(&FreeSans12pt7b);
    char tempStr[16];
    snprintf(tempStr, sizeof(tempStr), "%d / %d",
             forecast[i].tempMax, forecast[i].tempMin);
    int16_t bx, by; uint16_t bw, bh;
    display.getTextBounds(tempStr, 0, 0, &bx, &by, &bw, &bh);
    display.setCursor(x + 64 - bw, baseY + 135);
    display.print(tempStr);
    display.setFont();
  }
}

void drawTrains(Train trainsCentral[], int trainCountCentral, Train trainsHS[], int trainCountHS) {
  int baseY = TRAINS_Y;

  // Pick which set to display: Centraal preferred, fall back to HS.
  Train* trains = nullptr;
  int    trainCount = 0;
  const char* fromName = STATION_NAME_CENTRAL;
  const char* note = nullptr;

  if (trainCountCentral > 0) {
    trains = trainsCentral; trainCount = trainCountCentral;
    fromName = STATION_NAME_CENTRAL;
  } else if (trainCountCentral == 0 && trainCountHS > 0) {
    trains = trainsHS; trainCount = trainCountHS;
    fromName = STATION_NAME_HS;
  } else if (trainCountCentral == 0) {
    note = "No trains from Centraal or HS";
  } else {
    note = "Centraal data unavailable";
  }

  // Single-line header: "DH Centraal -> Breda"
  display.setFont(&FreeSans12pt7b);
  display.setCursor(MARGIN_LEFT, baseY + 20);
  display.print("DH ");
  display.print(fromName);
  display.print(" -> ");
  // Capitalize first letter of TRAIN_DESTINATION for display
  if (TRAIN_DESTINATION && TRAIN_DESTINATION[0]) {
    display.print((char)toupper((unsigned char)TRAIN_DESTINATION[0]));
    display.print(TRAIN_DESTINATION + 1);
  }
  display.setFont();

  if (note) {
    display.setFont(&FreeSans9pt7b);
    display.setCursor(MARGIN_LEFT, baseY + 55);
    display.print(note);
    display.setFont();
    return;
  }

  for (int i = 0; i < min(3, trainCount); i++) {
    int x = MARGIN_LEFT + i * TRAIN_SPACING;

    // Time
    display.setFont(&FreeSans18pt7b);
    display.setCursor(x, baseY + 60);
    display.print(trains[i].time);
    display.setFont();

    // Track
    display.setFont(&FreeSans9pt7b);
    display.setCursor(x, baseY + 82);
    display.print("Track ");
    display.print(trains[i].track);
    display.setFont();

    // Delay
    if (trains[i].delay[0] != 0) {
      display.setFont(&FreeSans9pt7b);
      display.setCursor(x, baseY + 100);
      display.print(trains[i].delay);
      display.setFont();
    }

    // Cancelled overlay
    if (trains[i].cancelled) {
      display.fillRect(x, baseY + 42, 110, 20, WHITE);
      display.setFont(&FreeSans9pt7b);
      display.setCursor(x, baseY + 58);
      display.print("CANCELLED");
      display.setFont();
    }
  }
}

void drawFooter() {
  // Left side: "Updated HH:MM" from current local time
  struct tm timeinfo;
  if (getLocalTime(&timeinfo)) {
    char buf[24];
    sprintf(buf, "Updated %02d:%02d", timeinfo.tm_hour, timeinfo.tm_min);
    display.setFont(&FreeSans9pt7b);
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
    float rainData[], char timeLabels[][20], int rainCount, bool rainOk,
    DayForecast forecast[], int forecastCount, bool forecastOk,
    Train trainsCentral[], int trainCountCentral,
    Train trainsHS[], int trainCountHS
  ) {
  
  display.clearDisplay();
  display.setRotation(0);

  // Get current time for header and day/night detection
  struct tm timeinfo;
  bool hasTime = getLocalTime(&timeinfo);
  
  // Header
  if (hasTime) {
    drawHeader(timeinfo, forecast, forecastCount, forecastOk);
  }

  // Section 1: Current Weather + Rain Chart
  if (weatherOk && hasTime) {
    bool isNight = (timeinfo.tm_hour >= 20 || timeinfo.tm_hour < 6);
    drawCurrentWeather(temp, currentWeatherCode, wind, isNight);
  } else {
    display.setFont(&FreeSans12pt7b);
    display.setCursor(MARGIN_LEFT, WEATHER_Y + 60);
    display.print("Weather Sync Error");
    display.setFont();
  }
  
  if (rainOk && rainCount > 0) {
    drawRainChart(rainData, timeLabels, rainCount);
  }
  
  // Divider line
  display.drawLine(MARGIN_LEFT, WEATHER_DIVIDER_Y, MARGIN_RIGHT, WEATHER_DIVIDER_Y, BLACK);

  // Section 2: Week Forecast
  if (forecastOk) {
    drawWeekForecast(forecast, forecastCount);
  }
  
  // Divider line
  display.drawLine(MARGIN_LEFT, FORECAST_DIVIDER_Y, MARGIN_RIGHT, FORECAST_DIVIDER_Y, BLACK);

  // Section 3: Trains
  drawTrains(trainsCentral, trainCountCentral, trainsHS, trainCountHS);

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