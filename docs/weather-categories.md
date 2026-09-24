# Weather categories: plan (not built yet)

Status, 24 September 2026: agreed in outline, not implemented. The greeting's
wording is left for later. Everything here is server-only (`server/screen/`).

## Why

The week row's icon comes from Open-Meteo's *daily* values, and those are all
midnight-to-midnight and pessimistic (read in Open-Meteo's source):

- The daily `weather_code` is the worst hour of 24, nights included: a cloudy
  night makes a sunny day overcast, one misty hour makes it fog, one uncertain
  hour makes it a thunderstorm.
- `precipitation_hours` counts every hour with more than 0.001 mm, so traces
  make a dry day "Drizzly".
- Night rain counts as much as rain at 08:00 (29 September 2026: 8.1 mm, nearly
  all before 07:00, on a day with sun from start to end, showed as rain).

Other apps judge daytime hours instead: Pirate Weather (blocks of the day, most
common cloud level), Breezy Weather (06:00-18:00). They show rain quickly (0.25
or 1 mm); this plan deliberately doesn't: **the icon shows what most of the day
is like.**

## The rules

**NOW** keeps showing the weather now, from the Buienradar stations.

**Each day of the week row, Today included,** is judged on the whole window
07:00-21:00. Open-Meteo's hourly rain and sunshine are for the hour *before* the
timestamp, so the window is the values stamped 08:00 to 21:00. Rain, snow, fog
and thunder count over the whole window; the sky counts only daylight hours
(at least half an hour between sunrise and sunset).

Per hour:
- **wet:** precipitation >= 0.3 mm
- **snow:** snowfall >= 0.1 cm (0.07 cm traces made a sunny 6 January snowy)
- **fog:** code 45/48; **thunder:** code 95-99
- **sky**, daylight hours only:
  - **clear:** >= 45 min sunshine **and** cloud < 50%
  - **overcast:** < 15 min sunshine **and** cloud >= 80%
  - **partly cloudy:** everything else, including sun through high cloud

Per day, first match wins:

| # | Result | When |
|---|---|---|
| 1 | Snow | >= 2 snow hours |
| 2 | Thunderstorm | >= 2 thunder hours |
| 3 | Rain (drizzle < 3 mm, rain >= 3 mm, heavy >= 10 mm) | >= 5 wet hours |
| 4 | Fog | >= 3 fog hours |
| 5 | Rain as in 3 | 2-4 wet hours on an overcast day |
| 6 | **Sun and showers** (new icon) | 2-4 wet hours otherwise |
| 7 | The sky | clear or overcast if more than half the daylight hours are; else partly cloudy |

Why the sky needs both sunshine and cloud: KNMI's model often gives 95-100%
cloud and a full hour of sunshine in the same hour (thin high cloud). Cloud
alone called 24 September overcast, sunshine alone called it clear; it was "a
lot of sun and a bit of cloud", which is partly cloudy.

## The sun-and-showers icon

Material Symbols has no sun-with-rain glyph. Draw it from two we have: `rainy`
at 86% size, bottom left, and `sunny` at 60% (weight 600, so its strokes match)
top right, with the sun knocked out around the cloud's silhouette plus a gap.
Reads at 48 px in greyscale and 1-bit. Make it a little bigger than the draft.

Also in NOW: Buienradar `f`, `h`, `k` ("afwisselend bewolkt met (lichte) regen")
use it by day; plain rain by night.

## Buienradar icon table fixes (NOW)

`q` (zwaar bewolkt en regen) rain, not heavy rain; `m` (zwaar bewolkt met wat
lichte regen) drizzle, not heavy rain; `l` rain, not thunderstorm; `t` (zware
sneeuwval) snow, was missing; `j` (opklaringen en hoge bewolking) partly
cloudy, not clear (as Home Assistant / python-buienradar do). Log codes not seen
before with the feed's `weatherdescription`.

## Data

- One Open-Meteo request, refreshed hourly (KNMI's model runs hourly), with
  hourly `temperature_2m, weather_code, precipitation, snowfall,
  sunshine_duration, cloud_cover, visibility` for 7 days, plus the daily values
  the greeting still uses (highs and lows, feels-like, sunrise and sunset,
  wind, gusts, UV). The 24-hour chart slices the same list, which also ends the
  chart vanishing when a fetch fails just after the hour.
- **Ask for UTC (`timezone=GMT`) and convert each timestamp with `zoneinfo`.**
  Open-Meteo labels a whole response with the offset in effect *when asked*
  (`timezone.secondsFromGMT()` in its source): the January data asked for in
  September came back as GMT+2, an hour off. Live, every forecast that spans a
  DST switch is an hour off after it. Today's code has this too (sunrise and
  sunset strings, the hourly list) for the days after a switch.
- Open-Meteo's `best_match` for Delft already layers KNMI HARMONIE (2 km, ~60 h)
  over DWD ICON and ECMWF: no need to go to KNMI directly. KNMI's model has no
  instability fields, so the first ~2.5 days never get thunderstorm codes;
  NOW still shows live thunder.

## Checking it

Fixtures in `server/tests/fixtures/`:
- `om_week1/`: the live forecast of 24 September 2026 (dry week, high cloud).
- `om_snow_2026_01/`: 4-8 January 2026 from the Historical Forecast API, with
  KNMI's measured hours (Voorschoten 215, Hoek van Holland 330) in
  `knmi_obs.json`.

Results so far, old rules vs new (measured, 07:00-21:00, in brackets):

| Day | Old | New | Measured |
|---|---|---|---|
| Thu 24 Sep | clear | partly cloudy | "a lot of sun, a bit of cloud" |
| Mon 28 Sep | rain | sun and showers | (forecast) rain 08-11, then sun |
| Tue 29 Sep | rain | partly cloudy | (forecast) rain before 07:00 |
| Wed 30 Sep | drizzle | overcast | (forecast) 0.2 mm in the window |
| Sun 4 Jan | rain | rain | 4.6 mm, 5 wet hours, 0.5 h sun |
| Mon 5 Jan | snow | snow | snow in the morning, 3.3 h sun |
| Tue 6 Jan | partly cloudy | sun and showers | 1.2 mm in the morning, 4.0 h sun |
| Wed 7 Jan | snow | snow | 8.1 mm in 10 hours, no sun |
| Thu 8 Jan | partly cloudy | sun and showers | 0.4 mm, 1.0 h sun, overcast: the model's sun was wrong |

Still to test with real data: fog, thunderstorms, a showery spring week, a grey
autumn day, a storm (links in the conversation of 24 September; the Historical
Forecast API with `timezone=GMT`, and `daggegevens.knmi.nl/klimatologie/uurgegevens`
for the measurements).

Building it: tests per rule and edge, a log line per day with the numbers behind
the choice, a preview before merging.

## Later: the greeting

Not good yet; wording to be decided. Directions: tie it to the day's category,
compare temperature with the season, switch to tomorrow in the evening, keep
the special days.
