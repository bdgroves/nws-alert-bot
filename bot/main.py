"""
NWS Alert Bot — Puget Sound edition
===================================
Posts only what matters for King & Pierce County, WA:

  * NWS warnings and watches that cover King or Pierce County (📍), plus
    Air Quality Alerts (wildfire smoke).
  * Elsewhere in Washington, only the high-impact warnings (tornado, flash
    flood, tsunami, blizzard, ice storm, extreme heat/wind, fire, evacuation,
    volcano).
  * Earthquakes M3.0+ in western Washington.

One post per hazard, not per product: NWS updates, extensions and
continuations of the same event never repost, and zones that share a hazard
in the same run are combined into one post.

River flooding is covered by the NWS Flood Warnings for the river forecast
points, which carry the stage and crest, so the bot no longer polls gauges.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from core import (  # noqa: E402
    Cache, Poster, alert_end, alert_key, fetch_nws, fmt_pacific, format_alert,
    log, now_utc,
)

UA = "NWSAlertBot/3.0 (github.com/bdgroves/nws-alert-bot)"

# King (053033) and Pierce (053053) counties, by FIPS and by forecast zone.
LOCAL_SAME = {"053033", "053053"}
LOCAL_ZONES = {"WAZ556", "WAZ558", "WAZ559", "WAZ560", "WAZ561", "WAZ527", "WAZ528"}

# Local alerts: every warning and watch, plus these.
LOCAL_EXTRA = {"Air Quality Alert", "Extreme Heat Advisory"}
# Local events that aren't worth a post even though they are warnings.
LOCAL_SKIP = {"Gale Warning", "Hazardous Seas Warning", "Small Craft Advisory"}

# Rest of Washington: high-impact warnings only.
STATE_EVENTS = {
    "Tornado Warning", "Severe Thunderstorm Warning", "Flash Flood Warning",
    "Tsunami Warning", "Blizzard Warning", "Ice Storm Warning",
    "Extreme Wind Warning", "Extreme Heat Warning", "Excessive Heat Warning",
    "Fire Warning", "Evacuation Immediate", "Civil Emergency Message",
    "Volcano Warning", "Earthquake Warning",
}

EMOJI = {
    "Tornado": "🌪️", "Thunderstorm": "⛈️", "Flash Flood": "🌊", "Flood": "💧",
    "Tsunami": "🌊", "Winter Storm": "❄️", "Blizzard": "🌨️", "Ice Storm": "🧊",
    "Wind": "💨", "Heat": "🌡️", "Red Flag": "🔥", "Fire": "🔥",
    "Air Quality": "😷", "Avalanche": "🏔️", "Volcano": "🌋", "Freeze": "🥶",
    "Cold": "🥶", "Evacuation": "🚨", "Civil": "🚨", "Snow": "❄️",
}

EQ_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
EQ_MIN_MAG = 3.0
EQ_BBOX = dict(minlatitude=46.0, maxlatitude=49.5, minlongitude=-125.0, maxlongitude=-120.0)


def emoji_for(event: str) -> str:
    return next((e for k, e in EMOJI.items() if k in event), "⚠️")


def is_local(p: dict) -> bool:
    same = set((p.get("geocode") or {}).get("SAME", []))
    ugc = set((p.get("geocode") or {}).get("UGC", []))
    return bool(same & LOCAL_SAME or ugc & LOCAL_ZONES)


def local_areas(p: dict) -> list[str]:
    """The King/Pierce zone names from an alert's area list."""
    names = [a.strip() for a in (p.get("areaDesc") or "").split(";")]
    ugc = (p.get("geocode") or {}).get("UGC", [])
    hits = []
    if len(ugc) == len(names):  # areaDesc lists zones in UGC order
        hits = [n for n, z in zip(names, ugc) if z in LOCAL_ZONES]
    if not hits:
        hits = [n for n in names if any(w in n.lower() for w in
                ("king", "pierce", "seattle", "tacoma", "bellevue", "rainier"))]
    return hits or ["King & Pierce Co."]


def wants(p: dict) -> str | None:
    """'local', 'state', or None."""
    ev = p.get("event", "")
    end = alert_end(p)
    if end and end < now_utc():
        return None
    if is_local(p):
        if ev in LOCAL_SKIP:
            return None
        if ev.endswith(("Warning", "Watch")) or ev in LOCAL_EXTRA or ev in STATE_EVENTS:
            return "local"
        return None
    if ev in STATE_EVENTS:
        return "state"
    return None


def run_alerts(poster: Poster) -> None:
    try:
        alerts = fetch_nws("WA", UA)
    except Exception as e:
        log.error(f"NWS fetch failed: {e}")
        return
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for p in alerts:
        tier = wants(p)
        if not tier:
            continue
        key = alert_key(p, "wa")
        if not key:
            continue
        p["_key"] = key
        groups[(tier, p.get("event", ""))].append(p)
    log.info(f"NWS: {sum(len(v) for v in groups.values())} postable products "
             f"in {len(groups)} hazards")

    # Local first, so the cap never crowds out what's overhead.
    for (tier, event), plist in sorted(groups.items(), key=lambda kv: kv[0][0] != "local"):
        fresh = [p for p in plist if p["_key"] not in poster.cache]
        if not fresh:
            continue
        if tier == "local":
            areas = [a for p in fresh for a in local_areas(p)]
            flag, tags = " 📍", "#WAwx"
        else:
            areas = [a.strip() for p in fresh for a in (p.get("areaDesc") or "").split(";")]
            flag, tags = "", "#WAwx"
        text = format_alert(event, emoji_for(event), flag, areas, fresh, tags)
        keys = [p["_key"] for p in fresh]
        poster.post(f"nws-{tier}", text, keys[0], also_mark=tuple(keys[1:]))


def run_quakes(poster: Poster) -> None:
    import requests
    start = now_utc() - timedelta(hours=24)
    params = dict(format="geojson", minmagnitude=EQ_MIN_MAG, orderby="time",
                  starttime=start.strftime("%Y-%m-%dT%H:%M:%S"), **EQ_BBOX)
    try:
        r = requests.get(EQ_URL, params=params, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        feats = r.json().get("features", [])
    except Exception as e:
        log.error(f"USGS fetch failed: {e}")
        return
    log.info(f"USGS: {len(feats)} quakes M{EQ_MIN_MAG}+ in 24 h")
    for f in sorted(feats, key=lambda f: f["properties"].get("time", 0)):
        p = f["properties"]
        if p.get("type") != "earthquake":
            continue
        key = f"eq:{f.get('id')}"
        mag = p.get("mag") or 0
        depth = (f.get("geometry", {}).get("coordinates") or [0, 0, 0])[2]
        when = fmt_pacific(datetime.fromtimestamp(p["time"] / 1000, timezone.utc), with_day=True)
        felt = f" · {p['felt']} felt reports" if p.get("felt") else ""
        icon = "🚨" if mag >= 5 else "📳" if mag >= 4 else "🌋"
        text = (f"{icon} M{mag:.1f} earthquake — {p.get('place', 'western WA')}\n"
                f"{when} · {depth:.0f} km deep{felt}\n"
                f"{p.get('url', '')}\n#WAwx #earthquake")
        poster.post("quake", text, key)


def main() -> int:
    log.info("=== NWS Alert Bot (Puget Sound) ===")
    cache = Cache()
    poster = Poster(cache)
    for step in (run_alerts, run_quakes):
        try:
            step(poster)
        except Exception as e:  # one broken feed never stops the other
            log.exception(f"{step.__name__} crashed: {e}")
    return poster.finish()


if __name__ == "__main__":
    sys.exit(main())
