# 🌩️ NWS Alert Bot

An automated bot that monitors **all NWS offices nationwide** and posts weather alerts to X/Twitter — similar to the [IEMBot](https://x.com/iembot_epz) network. Runs on **GitHub Actions** (free, no server required) and uses **[pixi](https://pixi.sh)** for reproducible dependency management.

---

## How It Works

```
Every 5 minutes:
  GitHub Actions (setup-pixi)
    → installs env from pixi.lock (cached — fast)
    → fetches active NWS alerts from api.weather.gov (no API key needed)
    → filters to alerts issued in the last 6 minutes
    → formats each into a ≤280-char tweet with emoji, area, expiry & link
    → posts to X/Twitter via Tweepy
    → commits posted_ids.json back to repo (bot's memory across runs)
```

---

## Setup Guide

### 1. Clone your repo locally

```bash
git clone git@github.com:bdgroves/nws-alert-bot.git
cd nws-alert-bot
```

### 2. Install pixi (if you haven't already)

```bash
curl -fsSL https://pixi.sh/install.sh | bash
# then restart your shell, or: source ~/.bashrc
```

### 3. Install the project environment

```bash
pixi install          # sets up the default env from pixi.lock
pixi install -e dev   # also installs dev tools (ruff, pytest)
```

### 4. Test it locally before deploying

```bash
# See how many alerts are active right now (no Twitter needed)
pixi run check

# Dry-run: fetch alerts and print what would be tweeted, without posting
pixi run dry-run
```

---

## Deploying to GitHub Actions

### 5. Add your Twitter API secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name               | Where to find it                        |
|---------------------------|-----------------------------------------|
| `TWITTER_API_KEY`         | Twitter Developer Portal → App → Keys  |
| `TWITTER_API_SECRET`      | Twitter Developer Portal → App → Keys  |
| `TWITTER_ACCESS_TOKEN`    | Twitter Developer Portal → App → Tokens|
| `TWITTER_ACCESS_SECRET`   | Twitter Developer Portal → App → Tokens|

> ⚠️ Your Twitter app must have **Read and Write** permissions. Regenerate tokens after changing app permissions.

### 6. Push & enable Actions

```bash
git add .
git commit -m "feat: initial NWS alert bot"
git push
```

Then go to the **Actions** tab in your repo, enable workflows if prompted, and optionally click **Run workflow** to trigger it immediately.

---

## File Structure

```
nws-alert-bot/
├── .github/
│   └── workflows/
│       └── nws-bot.yml     ← GitHub Actions: schedule + setup-pixi + run
├── bot/
│   └── main.py             ← Bot logic: fetch → format → tweet → cache
├── pyproject.toml          ← Pixi manifest + Python project metadata
├── pixi.lock               ← Auto-generated lockfile (commit this!)
├── posted_ids.json         ← Auto-updated alert cache (committed by bot)
├── .gitignore
└── README.md
```

---

## Pixi Tasks

| Command              | What it does                                     |
|----------------------|--------------------------------------------------|
| `pixi run bot`       | Run the bot (posts live tweets)                  |
| `pixi run dry-run`   | Fetch & format alerts, print only — no posting   |
| `pixi run check`     | Show count of active NWS alerts nationwide       |
| `pixi run lint`      | Lint with ruff (dev env)                         |
| `pixi run fmt`       | Auto-format with ruff (dev env)                  |
| `pixi run test`      | Run pytest (dev env)                             |

---

## Tweet Format

```
⛈️ Severe Thunderstorm Warning — Otero [NM]
Until 4:45 PM MDT
SEVERE TSTM WARNING...60 MPH WIND, 1.25 IN HAIL RADAR INDICATED
https://api.weather.gov/alerts/urn:oid:2.49.0.1.840...
```

Emoji is chosen automatically by event type (🌪️ tornado, ⛈️ tstm, 🌊 flash flood, ❄️ winter storm, etc.)

---

## Filtering to Specific Alert Types

By default the bot posts **all** NWS products. To restrict to certain events, add this inside `run()` in `bot/main.py` after the `fetch_alerts()` call:

```python
WANTED_EVENTS = {
    "Tornado Warning",
    "Severe Thunderstorm Warning",
    "Flash Flood Warning",
}
alerts = [a for a in alerts if a["properties"].get("event") in WANTED_EVENTS]
```

---

## Data Source

[NWS Alerts API](https://www.weather.gov/documentation/services-web-api) (`api.weather.gov`) — official, free, no API key required.

---

## GitHub Actions Cost

~8,640 runs/month × ~30 seconds each = **~72 compute-minutes/month**.
GitHub gives **2,000 free minutes/month** on public repos — this bot uses ~3.6% of that.
