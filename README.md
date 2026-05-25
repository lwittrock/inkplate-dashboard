# Inkplate 6 Weather & Train Dashboard

An e-paper dashboard for my Delft commute — wakes every 15 minutes, fetches live weather (Buienradar) and the next three trains from Den Haag to Tilburg Universiteit (NS), draws a full-screen editorial layout, then deep-sleeps until the next cycle.

![Dashboard mockup](design/mockup.png)

> *Mockup rendered from [design/mockup.html](design/mockup.html) — open at native 800×600 to see the source of truth.*

Personal hobby project, sharing in case it's useful as a reference. Not actively supporting forks — if you want to adapt it to your city/route, expect to read the code.

---

## Hardware

- **[Inkplate 6](https://inkplate.io/)** — ESP32-based, 800×600 1-bit e-ink display (Soldered Electronics)
- USB-C cable for flashing
- Optional: 3.7V LiPo battery (the Inkplate has an onboard charger)

That's it. No extra wiring.

---

## Flashing

1. **Install the Arduino IDE 2.x** and the Inkplate board package (board manager URL: `https://raw.githubusercontent.com/SolderedElectronics/Dasduino-Board-Definitions-for-Arduino-IDE/master/package_Dasduino_Boards_index.json`).
2. **Install libraries** via the IDE library manager:
   - `Inkplate` (by Soldered Electronics)
   - `ArduinoJson` **v7** (the sketch uses the v7 `JsonDocument` API; v6 will not compile)
3. **Copy `config.h.example` → `config.h`** and edit your location, station codes, and any other constants you care about.
4. **Create `secrets.h`** with your credentials:
   ```cpp
   const char* WIFI_SSID     = "your-network";
   const char* WIFI_PASSWORD = "your-password";
   const char* NS_API_KEY    = "your-ns-api-key";
   ```
   You'll need a free API key from [NS API portal](https://apiportal.ns.nl/) (the Reisinformatie subscription works for v3).
5. **Open `Dashboard.ino` in Arduino IDE.** All `.ino` files in the folder are compiled together.
6. **Select board:** `Inkplate6`, pick your COM port, hit **Upload**.

(Optional) Set `#define DEBUG_LOG 1` in `config.h` to get serial logs at 115200 baud.

For everything else — architecture, render pipeline, gotchas — see [CLAUDE.md](CLAUDE.md). It's written for an LLM but reads fine for humans and is the most up-to-date design doc.

---

## Attribution

- **[Inter](https://rsms.me/inter/)** by Rasmus Andersson — UI typeface, used under the SIL Open Font License 1.1. License text in [Fonts/OFL.txt](Fonts/OFL.txt). TTF→GFX conversion done via [rop.nl/truetype2gfx](https://rop.nl/truetype2gfx/).
- **[Buienradar](https://www.buienradar.nl/)** — current conditions (KNMI station observations) and 2-hour rain nowcast. Free; attribution required for commercial use, which this isn't.
- **[NS API](https://apiportal.ns.nl/)** — train departures via the Reisinformatie v3 Trip Planner endpoint.
- **[Open-Meteo](https://open-meteo.com/)** — 24h hourly temperature curve and 7-day forecast. Free, no key required.
- **[Inkplate](https://inkplate.io/)** hardware and library by Soldered Electronics.

---

## Security caveats

- All HTTPS calls use `WiFiClientSecure::setInsecure()` — no certificate validation. Acceptable for read-only public APIs on a private network; not acceptable if you adapt this to send anything sensitive.
- Your NS API key sits in `secrets.h` in plaintext on the device flash. Treat the device as physically trusted; if someone has it they can read the key.

---

## License

Code: MIT — see [LICENSE](LICENSE).
Inter font files in [Fonts/](Fonts/): SIL Open Font License 1.1 — see [Fonts/OFL.txt](Fonts/OFL.txt).
