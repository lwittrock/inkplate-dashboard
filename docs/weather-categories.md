# Weather categories

Status: live since 25 September 2026 (rules in `server/screen/weather.py`, the
request and parser in `sources.py`, the icon in `render.py`). The greeting's wording is still left for later; the new category
only got a placeholder, "Showery". Everything here is server-only (`server/screen/`).

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

**NOW** keeps showing the weather now, from the Buienradar stations, with one
correction for sunshine (below, "NOW: sun through high cloud").

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

Material Symbols has no sun-with-rain glyph. It is drawn from two we have
(`render._showers_mask`): `rainy` at 86% size, bottom left, and `sunny` at 64%
top right, 200 weight units heavier than the cloud so its strokes match (600 in
the week row, 500 in NOW), knocked out around the cloud's silhouette plus a gap
of 13% of the box. A ray the gap would cut is dropped whole, since a sliver of one
reads as a speck; only the disc is cut into an arc. Reads at 48 and 128 px, in
greyscale and 1-bit.

Also in NOW: Buienradar `f`, `h`, `k` ("afwisselend bewolkt met (lichte) regen")
use it by day; plain rain by night.

## Buienradar icon table fixes (NOW)

`q` (zwaar bewolkt en regen) rain, not heavy rain; `m` (zwaar bewolkt met wat
lichte regen) drizzle, not heavy rain; `l` rain, not thunderstorm; `t` (zware
sneeuwval) snow, was missing; `j` (opklaringen en hoge bewolking) partly
cloudy, not clear (as Home Assistant / python-buienradar do). `w` (regen en
winterse neerslag) stays snow. No code means heavy rain any more. Codes not seen
before are logged once, with the feed's `weatherdescription`, and shown as overcast.

## NOW: sun through high cloud

Added 25 September 2026, on trial for a week. Buienradar's icon follows total
cloud cover and ignores sunshine, so a veil of high cloud reads "zwaar bewolkt"
while the sun shines through it. That morning at 08:50, Rotterdam and Hoek van
Holland said `c` (Voorschoten `b`), so the vote said overcast under a mostly
blue sky; De Bilt said `c` while measuring 90% of a clear sky's sunshine, and
KNMI's model had 91% cloud, all of it high, with full sunshine.

**The rule** (`weather.pick_current`): an overcast vote shows **partly cloudy**
when all three hold:
- the sun is at least `MIN_SUN_ELEVATION` = 10 degrees up (lower, measured
  sunshine says little);
- the model's hour gives at least `MODEL_SUN_S` = 45 minutes of sun;
- the voting stations' median `sunpower` is at least `SUN_THROUGH` = 40% of a
  clear sky's (Haurwitz's model at the sun's height, `clear_sky_wm2`).

It never darkens a clear vote, and rain, snow, fog and thunder stay the
stations' call. The three numbers come from one morning: near the coast, at
11 degrees, a mostly blue sky measured 42-49% of the model's clear sky.

**What is kept.** Every render (every 5 minutes, not at night) appends one
JSON record to `now.jsonl` in the service's state directory, kept for 14 days
(`screen/nowlog.py`): the choice, the vote, the sun's height and a clear sky's
W/m², the voters' median share, the model's hour (sunshine, cloud and its
low, mid and high layers), and each of the six nearest stations with its
distance, code, W/m², share and whether it voted. The file survives restarts
and reboots. From the laptop:

    curl "http://192.168.1.212:8088/now-log?days=7" > now.jsonl

The journal gets one short line per render, and `/status` shows the latest:

    now: partly_cloudy (sun through; vote overcast; stations 80%, model 60 min sun, sun 22.1 deg up)

**Re-evaluate around 2 October 2026**: compare the week's records with KNMI's
measured hours for Voorschoten (215), Rotterdam (344) and Hoek van Holland
(330) at `daggegevens.knmi.nl/klimatologie/uurgegevens` (SQ sunshine, N
cloud), plus any moments the wall looked wrong. Questions: where does
`SUN_THROUGH` separate sunny hours from grey ones, does 10 degrees hold, and
does the model's high-cloud share help (it is logged, not used)?

## Data

- One Open-Meteo request, refreshed hourly (KNMI's model runs hourly) and kept
  for 12 hours when a fetch fails, with hourly `temperature_2m, weather_code,
  precipitation, snowfall, sunshine_duration, cloud_cover` for 7 days, plus
  the daily values the greeting still uses (highs and lows, feels-like, sunrise
  and sunset, wind, gusts, UV). The 24-hour chart and the week are cut from it
  at each render (`collect.hours_ahead`, `week_ahead`), which also ends the
  chart vanishing when a fetch fails just after the hour. Just after midnight
  with yesterday's copy, the week shows six days.
- **Timestamps: undo `utc_offset_seconds`, then convert with `zoneinfo`**
  (`sources.om_time`). Open-Meteo labels a whole response with the offset in
  effect *when asked* (`timezone.secondsFromGMT()` in its source): the January
  data asked for in September came back as GMT+2, an hour off (sunrise 09:50,
  really 08:50). Live, every forecast that spans a DST switch was an hour off
  after it. The request still asks for local time (`timezone=auto`), not GMT:
  in GMT the daily values would cover 02:00-02:00 local days, and between
  midnight and 02:00 the seventh day would have no hours. The daily highs and
  lows after a switch are aggregated over a day shifted by an hour; negligible.
- Open-Meteo's `best_match` for Delft already layers KNMI HARMONIE (2 km, ~60 h)
  over DWD ICON and ECMWF: no need to go to KNMI directly. KNMI's model has no
  instability fields, so the first ~2.5 days never get thunderstorm codes;
  NOW still shows live thunder.

## Checking it

Recorded data in `server/tests/data/` (not `tests/fixtures/`: the deploy's
self-test renders every folder there and needs a full recording with `meta.json`):
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

A consequence to check against the thunderstorm data: a summer day with one
hour of downpour or thunder (10 mm, say) and sun otherwise shows the sky, since
one wet or thundery hour doesn't count. That fits "what most of the day is
like", and is the rule most likely to be revisited.

Still to test with real data: fog, thunderstorms, a showery spring week, a grey
autumn day, a storm (links in the conversation of 24 September; the Historical
Forecast API with `timezone=GMT`, and `daggegevens.knmi.nl/klimatologie/uurgegevens`
for the measurements).

Built with tests per rule and edge (`tests/test_weather.py`), the table above
checked against both recordings (`tests/test_sources.py`), and a log line per
day and fetch with the counts behind the choice, e.g.
`2026-09-30: showers (wet 4 h, 6.9 mm, ... sky 0 clear/6 partly/5 overcast)`.
The rain amount (drizzle, rain, heavy) is the window's total.

## Later: the greeting

Not good yet; wording to be decided. Directions: tie it to the day's category,
compare temperature with the season, switch to tomorrow in the evening, keep
the special days.
