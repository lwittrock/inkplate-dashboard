// ============================================================================
// DISPLAY: the server's frame, or one line saying what failed
// ============================================================================

// One font, for the failure message only. Inter (OFL), via rop.nl/truetype2gfx.
#include "Fonts/Inter_Bold18pt7b.h"

// Draw the frame (600 rows of 100 bytes, MSB first, 1 = black) and refresh.
// Only black pixels are drawn onto a cleared buffer: the screen is mostly
// white, so skipping zero bytes keeps this fast at 80 MHz.
void drawFrame(const uint8_t* frame, bool fullRefresh) {
  display.clearDisplay();
  display.setRotation(0);
  for (int y = 0; y < 600; y++) {
    const uint8_t* row = frame + y * 100;
    for (int xb = 0; xb < 100; xb++) {
      uint8_t b = row[xb];
      if (!b) continue;
      for (int bit = 0; bit < 8; bit++) {
        if (b & (0x80 >> bit)) display.drawPixel(xb * 8 + bit, y, BLACK);
      }
    }
  }
  // Full refreshes clear the ghosting partial ones leave; the server decides
  // which (every 4th wake, after a failure, after new firmware).
  if (fullRefresh) {
    DBGLN("Full refresh");
    display.display();
  } else {
    DBGLN("Partial refresh");
    display.partialUpdate();
  }
}

// The whole screen for a failure: one centered line, nothing else, and a
// full refresh so no trace of the dashboard is left to mislead.
void drawMessage(const char* text) {
  display.clearDisplay();
  display.setRotation(0);
  display.setFont(&Inter_Bold18pt7b);
  display.setTextColor(BLACK);
  int16_t bx, by;
  uint16_t bw, bh;
  display.getTextBounds(text, 0, 0, &bx, &by, &bw, &bh);
  display.setCursor((800 - (int)bw) / 2 - bx, (600 - (int)bh) / 2 - by);
  display.print(text);
  display.display();
}
