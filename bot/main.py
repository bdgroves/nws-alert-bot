"""
NWS Alert Bot - Monitors NWS alerts nationwide and posts to X/Twitter.
Powered by the Iowa Environmental Mesonet (IEM) API.
"""

import os
import sys
import json
import time
import logging
import hashlib
import requests
import tweepy
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# IEM CAP/Atom feed — nationwide, all products, last 5 minutes
IEM_API_URL = "https://mesonet.agron.iastate.edu/api/1/nwstext.json"

# NWS Alerts API (api.weather.gov) — nationwide active alerts
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"

# How far back to look for alerts (minutes).
# GitHub Actions runs every 5 min; use 6 min window to avoid edge-case gaps.
LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "6"))

# Maximum tweet length (X allows 280 chars)
MAX_TWEET_LEN = 280

# Path to the posted-IDs cache file (lives in the repo via artifact / env)
CACHE_FILE = os.environ.get("CACHE_FILE", "posted_ids.json")


# ── Twitter client ────────────────────────────────────────────────────────────

def get_twitter_client() -> tweepy.Client:
    """Build an authenticated Tweepy v2 client from environment secrets."""
    required = [
        "TWITTER_API_KEY",
        "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_SECRET",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise EnvironmentError(f"Missing Twitter credentials: {', '.join(missing)}")

    return tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_SECRET"],
    )


# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_cache() -> set:
    """Load the set of already-posted alert IDs from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            return set(data.get("posted", []))
        except (json.JSONDecodeError, KeyError):
            log.warning("Cache file corrupt, starting fresh.")
    return set()


def save_cache(posted: set) -> None:
    """Persist posted IDs to disk. Keep only the last 2000 to avoid bloat."""
    recent = list(posted)[-2000:]
    with open(CACHE_FILE, "w") as f:
        json.dump({"posted": recent, "updated": datetime.utcnow().isoformat()}, f)


# ── NWS fetch ─────────────────────────────────────────────────────────────────

def fetch_alerts(lookback_minutes: int) -> list[dict]:
    """
    Pull active NWS alerts from api.weather.gov.
    Returns a list of alert feature dicts.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    params = {
        "status": "actual",
        "message_type": "alert,update,cancel",
        "limit": 500,
    }
    headers = {
        "User-Agent": "NWSAlertBot/1.0 (github-actions; contact via repo)",
        "Accept": "application/geo+json",
    }

    try:
        resp = requests.get(NWS_ALERTS_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.error(f"Failed to fetch NWS alerts: {e}")
        return []

    features = data.get("features", [])
    log.info(f"Fetched {len(features)} total active alerts from NWS.")

    # Filter to alerts sent/updated within the lookback window
    recent = []
    for f in features:
        props = f.get("properties", {})
        sent_str = props.get("sent") or props.get("effective")
        if not sent_str:
            continue
        try:
            sent_dt = datetime.fromisoformat(sent_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if sent_dt >= cutoff:
            recent.append(f)

    log.info(f"{len(recent)} alerts within the last {lookback_minutes} minutes.")
    return recent


# ── Tweet formatting ───────────────────────────────────────────────────────────

# Friendly short names for NWS event types
EVENT_EMOJI = {
    "Tornado Warning": "🌪️",
    "Tornado Watch": "🌪️",
    "Severe Thunderstorm Warning": "⛈️",
    "Severe Thunderstorm Watch": "⛈️",
    "Flash Flood Warning": "🌊",
    "Flash Flood Watch": "🌊",
    "Flood Warning": "💧",
    "Flood Watch": "💧",
    "Winter Storm Warning": "❄️",
    "Winter Storm Watch": "❄️",
    "Blizzard Warning": "🌨️",
    "Ice Storm Warning": "🧊",
    "High Wind Warning": "💨",
    "High Wind Watch": "💨",
    "Dust Storm Warning": "🌫️",
    "Excessive Heat Warning": "🔥",
    "Heat Advisory": "☀️",
    "Special Weather Statement": "📢",
    "Hazardous Weather Outlook": "📋",
}

DEFAULT_EMOJI = "⚠️"


def alert_to_unique_id(props: dict) -> str:
    """Generate a stable unique ID for an alert."""
    raw = (props.get("id") or props.get("@id") or
           f"{props.get('event','')}-{props.get('sent','')}-{props.get('areaDesc','')}")
    return hashlib.md5(raw.encode()).hexdigest()


def format_tweet(props: dict) -> str:
    """
    Format an NWS alert into a tweet ≤280 characters.

    Structure:
      {emoji} {EVENT} — {areas}
      Expires: {time} {tz}
      {headline or short description}
      {url}
    """
    event = props.get("event", "Weather Alert")
    emoji = EVENT_EMOJI.get(event, DEFAULT_EMOJI)
    area = props.get("areaDesc", "Unknown area")
    headline = (props.get("parameters", {}).get("NWSheadline", [""])[0]
                or props.get("headline", "")
                or props.get("description", "")[:120])
    headline = headline.strip()

    # Expiry time
    expires_str = props.get("expires") or props.get("ends") or ""
    expires_label = ""
    if expires_str:
        try:
            exp_dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            expires_label = f"Until {exp_dt.strftime('%I:%M %p %Z').lstrip('0')}"
        except ValueError:
            pass

    # NWS detail URL — use the @id link
    alert_id_url = props.get("@id", "")
    vtec_id = ""
    for vtec in (props.get("parameters", {}).get("VTEC") or []):
        vtec_id = vtec
        break

    # Build the tweet in parts, trimming headline to fit
    url_part = f"\n{alert_id_url}" if alert_id_url else ""
    expires_part = f"\n{expires_label}" if expires_label else ""

    header = f"{emoji} {event} — {area}"
    if len(header) > 100:
        header = f"{emoji} {event} — {area[:80]}…"

    # Calculate remaining space for headline
    skeleton = f"{header}{expires_part}\n\n{url_part}"
    remaining = MAX_TWEET_LEN - len(skeleton) - 2  # 2 for newlines

    if headline and remaining > 20:
        if len(headline) > remaining:
            headline = headline[:remaining - 1] + "…"
        body = f"{header}{expires_part}\n{headline}{url_part}"
    else:
        body = f"{header}{expires_part}{url_part}"

    # Final safety trim
    if len(body) > MAX_TWEET_LEN:
        body = body[:MAX_TWEET_LEN - 1] + "…"

    return body


# ── Main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    log.info("=== NWS Alert Bot starting ===")

    dry_run_global = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    if dry_run_global:
        log.info("[DRY RUN MODE] No tweets will be posted.")
        client = None
    else:
        client = get_twitter_client()

    posted = load_cache()
    log.info(f"Cache contains {len(posted)} previously posted IDs.")

    # Fetch alerts
    alerts = fetch_alerts(LOOKBACK_MINUTES)
    if not alerts:
        log.info("No new alerts found. Exiting.")
        return

    new_count = 0
    error_count = 0

    for feature in alerts:
        props = feature.get("properties", {})
        uid = alert_to_unique_id(props)

        if uid in posted:
            log.debug(f"Skipping already-posted alert: {uid}")
            continue

        tweet_text = format_tweet(props)
        log.info(f"Posting [{props.get('event','?')}] len={len(tweet_text)}")
        log.debug(f"Tweet:\n{tweet_text}")

        dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
        if dry_run:
            log.info("[DRY RUN] Would post:\n%s", tweet_text)
            posted.add(uid)
            new_count += 1
            continue

        try:
            client.create_tweet(text=tweet_text)
            posted.add(uid)
            new_count += 1
            # Rate-limit courtesy delay between tweets
            time.sleep(2)
        except tweepy.TweepyException as e:
            log.error(f"Twitter API error: {e}")
            error_count += 1
            if "453" in str(e) or "187" in str(e):
                # Duplicate tweet or access level issue — skip
                posted.add(uid)
        except Exception as e:
            log.error(f"Unexpected error posting tweet: {e}")
            error_count += 1

    save_cache(posted)
    log.info(f"Done. Posted: {new_count} | Errors: {error_count} | Cache size: {len(posted)}")


if __name__ == "__main__":
    run()
