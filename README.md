# 🌧️ NWS Alert Bot — Pacific Northwest Enhanced

> *"The Sound doesn't warn you either."*

---

An autonomous weather monitoring bot for the **Pacific Northwest** — with a special focus on **King and Pierce County, WA**. Built on the same architecture as [@SierraNevadaWX](https://twitter.com/SierraNevadaWX).
Runs on a GitHub Actions schedule. No server. No cost. Just the data.

---

---

## 📡 What It Posts

Only what matters. One post per hazard, never one per bulletin.

| Source | Posts when | Coverage |
|--------|-----------|----------|
| 🌩️ **NWS warnings & watches** 📍 | A new warning or watch covers King or Pierce County, or an Air Quality Alert (smoke) | King & Pierce County |
| 🚨 **NWS high-impact warnings** | Tornado, severe thunderstorm, flash flood, tsunami, blizzard, ice storm, extreme wind or heat, fire, evacuation, volcano | Rest of Washington |
| 🌋 **USGS earthquakes** | M3.0+ | 46–49.5°N, 125–120°W |

What it deliberately leaves out: advisories and Special Weather Statements, updates and extensions of a hazard it already posted, Oregon/California/Nevada (the [Sierra bot](https://github.com/bdgroves/sierra-alert-bot) covers those), the 3-hourly temperature posts and the twice-daily balloon summaries.

River flooding comes through the NWS **Flood Warnings** for the Green, White, Puyallup, Cedar and Snoqualmie forecast points, which carry the stage and expected crest.

**How "one post per hazard" works:** NWS hazards carry a VTEC code with an event number. The bot posts when a hazard is new (or its area grows) and ignores continuations, extensions and cancellations. Zones that share a hazard in the same run are combined into one post. At most 6 posts per run; anything beyond that waits for the next run.

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

## 🛠️ Setup

```bash
git clone https://github.com/bdgroves/nws-alert-bot
cd nws-alert-bot
pixi install
```

**GitHub Actions secrets:** `TWITTER_API_KEY`, `TWITTER_API_SECRET`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_SECRET` (X Developer Portal → your app → Keys and tokens).

```bash
pixi run dry-run          # log what would post, post nothing
pixi run bot              # live run
pip install pytest requests && pytest -q tests   # offline tests, fake feeds
```

From the Actions tab, **Run workflow** with *Dry run* ticked shows what would post without posting.

---

## 🩺 Is it working?

Every run commits its own record, so you never need the Actions log:

- `logs/last_run.log` — the full log of the latest run
- `logs/posts.jsonl` — every post attempt (posted, held, or the exact X error)
- `posted_ids.json` → `state` — `x_ok`, `x_error`, `x_error_since`, and `x_check` (a no-post check of the X keys, run on the first run of a new cache)

If X starts refusing posts, the run **fails once** so GitHub emails you, then keeps logging quietly until posting works again.

**Standing by:** X posting is off while the X developer account has no API credits (X answers "402 Payment Required: credits depleted"). The bot keeps watching every feed and logs what it *would* have posted to `logs/posts.jsonl`, marking each item as seen, so nothing stale floods out later. To turn X back on, set the repository variable `POST_TO_X` to `on` (Settings → Secrets and variables → Actions → Variables).

---

## 📋 Post formats

**NWS alert (King/Pierce):**
```
❄️ Winter Storm Warning 📍
Seattle and Vicinity; Tacoma Area
Until Tue 4 PM PST
Heavy snow. Total accumulations of 6 to 10 inches.
https://www.weather.gov/sew/
#WAwx
```

**Earthquake:**
```
🌋 M3.4 earthquake — 5 km N of Tacoma, WA
Sun 1:12 PM PDT · 22 km deep · 41 felt reports
https://earthquake.usgs.gov/earthquakes/eventpage/...
#WAwx #earthquake
```

---

## 🏗️ Architecture

```
nws-alert-bot/
├── bot/
│   ├── main.py              # What to post (sources and filters)
│   └── core.py              # Cache, posting, run log (shared with sierra-alert-bot)
├── tests/test_bot.py        # Offline tests with fake feeds and a fake X client
├── logs/                    # last_run.log + posts.jsonl, committed each run
├── .github/workflows/
│   ├── nws-bot.yml          # Schedule + manual dry run
│   └── test.yml             # Tests on every code push
└── posted_ids.json          # Dedup cache + X status, committed each run
```

---

## 🛰️ Data Sources

| Data | Provider | Endpoint |
|------|----------|----------|
| Weather alerts | NWS | `api.weather.gov/alerts/active?area=WA` |
| Earthquakes | USGS | `earthquake.usgs.gov/fdsnws/event/1/query` |

---

## 📜 License

MIT. Fork it for your region.

---

*Built in Lakewood, WA, watching King and Pierce County.*
*[@bdgroves](https://twitter.com/bdgroves)*
