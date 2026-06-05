# 🌧️ NWS Alert Bot — Pacific Northwest Enhanced

> *"The Sound doesn't warn you either."*

---

An autonomous weather monitoring bot for the **Pacific Northwest** — with a special focus on **King and Pierce County, WA**. Built on the same architecture as [@SierraNevadaWX](https://twitter.com/SierraNevadaWX), expanded with upper air science, river flood monitoring, and earthquake detection.

Runs every 5 minutes on GitHub Actions. No server. No cost. Just the data.

---

## 📡 What It Monitors

| Source | Trigger | Coverage |
|--------|---------|----------|
| 🌩️ **NWS Weather Alerts** | Any alert within lookback window | WA, OR, CA, NV |
| 💧 **River Gauges** | At or above minor flood stage | Green, White, Puyallup, Cedar, Snoqualmie |
| 🌋 **USGS Earthquakes** | M2.5+ in Puget Sound region | 46-49.5°N, 125-120°W |
| 🌡️ **Surface Obs Snapshot** | Every ~3 hours | Seattle, Tacoma, Renton |
| 🎈 **Upper Air Sounding** | 30-90 min after 00Z/12Z launches | SEA (Seattle) balloon |
| 🔥 **NIFC Wildfires** | New fire ≥10 acres, <85% contained | King/Pierce extended bbox |
| 🌲🔥 **InciWeb** | New WA wildfire incident update | Washington state |
| 🌌 **NOAA Space Weather** | Geomagnetic storm G1+ alert | Nationwide (aurora visible PNW) |
| ⛵ **Marine Alerts** | Small Craft, Gale, Tsunami | Puget Sound marine zones |

**King & Pierce County alerts are flagged with 📍** and logged separately so you always know when something is headed your way.

---

## 🏔️ Why King & Pierce County

King County is home to Seattle, Bellevue, Redmond, Renton, Auburn, and Kent — 2.3 million people in a region bounded by Puget Sound, the Cascades, and five major river systems. Pierce County adds Tacoma, Puyallup, and Joint Base Lewis-McChord.

The hazards here are not subtle:

- **Atmospheric rivers** — the Pineapple Express can dump 5+ inches in 24 hours on the Cascades
- **River flooding** — the Green, White, Puyallup, Cedar, and Snoqualmie rivers flood regularly. The White River flooded 10 times in 15 years before the Howard Hanson Dam was completed
- **Earthquakes** — the Cascadia Subduction Zone. The Seattle Fault. The South Whidbey Island Fault. The region is seismically active
- **Snowpack release** — a warm "pineapple express" hitting a heavy snowpack produces rain-on-snow flooding that overwhelms reservoirs and levees
- **JBLM / Boeing / ports** — critical infrastructure concentration means weather events have outsized regional impact

**King County had FEMA-declared flooding in early 2026.** The deadline to apply for FEMA Individual Assistance was June 10, 2026. This bot was built during that recovery period.

---

## 🌊 The Rivers We Watch

| River | Station | Location | Minor Flood |
|-------|---------|----------|------------|
| Green River | grso3 | Auburn | TBD from NWPS |
| White River | whro3 | Buckley | TBD from NWPS |
| Cedar River | cdrw1 | Renton | TBD from NWPS |
| Snoqualmie River | snoo3 | Snoqualmie | TBD from NWPS |
| Puyallup River | puyallupnf | Puyallup | TBD from NWPS |

Flood stages pulled dynamically from the NWS NWPS API — no hardcoded thresholds. Tweets when observed stage reaches minor flood category.

---

## 🎈 The Balloon

Twice a day — at 00Z (5 PM PDT) and 12Z (5 AM PDT) — a weather balloon launches from Seattle-Tacoma International Airport. It rises through the troposphere, measuring temperature, humidity, and wind every few hundred feet, transmitting until it bursts at roughly 100,000 feet.

The `fetch_sounding_tweet()` function pulls that data from Iowa State's RAOB archive via Siphon and posts a summary 30-90 minutes after each launch. Key fields:

- **Surface conditions** — temperature and RH at launch
- **700mb temperature** — the key layer for PNW precipitation type. T700 below 0°C means snow level is low; below 2°C means mixed precip is possible at pass elevations
- **500mb temperature** — the steering level for Pacific weather systems
- **Max wind** — jet stream intensity

This is the same data WFO Seattle forecasters use to assess snowpack melt risk, atmospheric river depth, and convective instability. Now it's in your Twitter feed.

---

## 🛠️ Setup

```bash
git clone https://github.com/bdgroves/nws-alert-bot
cd nws-alert-bot
pixi install
```

**GitHub Actions secrets:**

| Secret | Where |
|--------|-------|
| `TWITTER_API_KEY` | Twitter Developer Portal → App → Keys |
| `TWITTER_API_SECRET` | Twitter Developer Portal → App → Keys |
| `TWITTER_ACCESS_TOKEN` | Twitter Developer Portal → App → Tokens |
| `TWITTER_ACCESS_SECRET` | Twitter Developer Portal → App → Tokens |

**Local testing:**
```bash
pixi run dry-run    # see what would be tweeted
pixi run check      # count active WA alerts
pixi run bot        # live run (posts to Twitter)
```

---

## 📋 Tweet formats

**NWS Alert (King/Pierce flagged):**
```
⛈️ Severe Thunderstorm Warning 📍 — King County
Until 4:45 PM PDT
60 MPH WIND AND PENNY SIZE HAIL
https://api.weather.gov/alerts/...
```

**River Flood Alert:**
```
💧 Green River at Auburn — Minor Flooding
Stage: 14.2 ft (minor flood = 13.0 ft)
#WAwx #KingCounty #PierceCounty
```

**Earthquake:**
```
🌋 M3.2 Earthquake — 12km SSE of Renton, WA
8.4km deep
https://earthquake.usgs.gov/earthquakes/...
#WAwx #earthquake #PNW
```

**Surface Obs (every ~3h):**
```
🌡️ King/Pierce Co. — 8:45 AM PDT
Seattle: 58°F  RH:82%  💨12mph
Tacoma: 56°F  RH:85%  calm
Renton: 57°F  RH:80%  💨8mph
#WAwx #Seattle #Tacoma
```

**Upper Air Sounding (near 00Z/12Z):**
```
🎈 Seattle Upper Air — 5:00 PM PDT
Sfc: 14.2°C  RH:72%
700mb: 2.1°C  500mb: -12.4°C  🌨️ Mixed precip
Max wind: 45kt
#WAwx #Seattle #upperair #PNW
```

---

## 🏗️ Architecture

```
nws-alert-bot/
├── bot/
│   └── main.py              # All data sources + tweet formatting
├── .github/
│   └── workflows/
│       └── nws-bot.yml      # GitHub Actions schedule (every 5 min)
├── posted_ids.json          # Auto-committed dedup cache
└── pyproject.toml           # pixi dependencies
```

**Runtime:** GitHub Actions free tier — ~72 compute-minutes/month (3.6% of 2,000 free)
**Language:** Python 3.12 · **Package manager:** pixi · **API:** Tweepy v4

---

## 🛰️ Data Sources

| Data | Provider | Endpoint |
|------|----------|----------|
| Weather Alerts | NWS | `api.weather.gov/alerts/active/area/{state}` |
| Surface Observations | NWS | `api.weather.gov/stations/{id}/observations/latest` |
| River Gauges | NWS NWPS | `api.water.noaa.gov/nwps/v1/gauges/{id}/stageflow` |
| Earthquakes | USGS | `earthquake.usgs.gov/fdsnws/event/1/query` |
| Upper Air Soundings | Iowa State RAOB | Siphon / IAStateUpperAir |

---

## 📜 License

MIT. Fork it for your region.

---

*Built in Lakewood, WA, watching King and Pierce County.*
*[@bdgroves](https://twitter.com/bdgroves)*
