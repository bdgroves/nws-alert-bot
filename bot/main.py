"""
NWS Alert Bot — Pacific Northwest Enhanced Edition
====================================================
Monitors NWS alerts for WA/OR/NV/CA with special focus on
King & Pierce County, WA. Also posts:

  - Upper air sounding summaries (SEA balloon via Iowa State RAOB)
  - Surface obs snapshots (SeaTac, Tacoma, Renton — every ~3h)
  - River gauge flood monitoring (Green, White, Puyallup, Cedar, Snoqualmie)
  - Earthquake monitoring for Puget Sound region M2.5+

King/Pierce alerts get a 📍 flag.
Sounding tweets post 30-90 min after 00Z / 12Z balloon launches.
"""

import os, sys, json, time, logging, hashlib, requests, tweepy
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
NWS_OBS_URL    = "https://api.weather.gov/stations/{}/observations/latest"
NWPS_GAUGE_URL = "https://api.water.noaa.gov/nwps/v1/gauges/{}/stageflow"
USGS_EQ_URL    = "https://earthquake.usgs.gov/fdsnws/event/1/query"

LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "6"))
MAX_TWEET_LEN    = 280
CACHE_FILE       = os.environ.get("CACHE_FILE", "posted_ids.json")
DRY_RUN          = os.environ.get("DRY_RUN", "").lower() in ("1","true","yes")
PACIFIC          = ZoneInfo("America/Los_Angeles")
HEADERS          = {"User-Agent": "NWSAlertBot/2.0 (github.com/bdgroves/nws-alert-bot)",
                    "Accept":     "application/geo+json"}
STATES           = ["WA", "OR", "CA", "NV"]

# ── Wildfire sources ──────────────────────────────────────────────────────────
# NIFC WFIGS — active fire perimeters nationwide
NIFC_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)
# InciWeb — incident information (text reports)
INCIWEB_URL = "https://inciweb.wildfire.gov/feeds/rss/incidents/"

# King/Pierce wildfire bbox — slightly wider to catch nearby fires
# Covers from the coast to the Cascades, Snohomish to Lewis County
KP_FIRE_BBOX = (-123.5, 46.7, -121.0, 48.2)
FIRE_MIN_ACRES = 10

# ── NOAA special products ─────────────────────────────────────────────────────
# NOAA Space Weather Center — geomagnetic storm alerts
SWPC_ALERT_URL = "https://services.swpc.noaa.gov/products/alerts.json"
# NWS marine zones for Puget Sound / Strait of Juan de Fuca
MARINE_ZONES = {"PZZ131", "PZZ132", "PZZ133", "PZZ134", "PZZ135"}

# NWS forecast zones — King & Pierce County WA and close neighbors
KING_PIERCE_ZONES = {
    "WAZ558", "WAZ559", "WAZ556", "WAZ560",
    "WAZ527", "WAZ528", "WAZ561",
}

# River gauges — King/Pierce County watersheds
RIVER_GAUGES = {
    "grso3":      {"name": "Green River",     "city": "Auburn"},
    "whro3":      {"name": "White River",      "city": "Buckley"},
    "cdrw1":      {"name": "Cedar River",      "city": "Renton"},
    "snoo3":      {"name": "Snoqualmie River", "city": "Snoqualmie"},
    "puyallupnf": {"name": "Puyallup River",   "city": "Puyallup"},
}

EVENT_EMOJI = {
    "Tornado Warning":"🌪️","Tornado Watch":"🌪️",
    "Severe Thunderstorm Warning":"⛈️","Severe Thunderstorm Watch":"⛈️",
    "Flash Flood Warning":"🌊","Flash Flood Watch":"🌊",
    "Flood Warning":"💧","Flood Watch":"💧","Flood Advisory":"💧",
    "Winter Storm Warning":"❄️","Winter Storm Watch":"❄️",
    "Blizzard Warning":"🌨️","Ice Storm Warning":"🧊",
    "High Wind Warning":"💨","High Wind Watch":"💨","Wind Advisory":"💨",
    "Excessive Heat Warning":"🔥","Heat Advisory":"☀️",
    "Red Flag Warning":"🔥","Fire Weather Watch":"🔥",
    "Dense Fog Advisory":"🌫️","Dust Storm Warning":"🌫️",
    "Small Craft Advisory":"⛵","Gale Warning":"🌬️",
    "Tsunami Warning":"🌊","Tsunami Watch":"🌊",
    "Air Quality Alert":"😷",
    "Special Weather Statement":"📢","Hazardous Weather Outlook":"📋",
}

# ─────────────────────────────────────────────────────────────────────────────
def get_twitter_client():
    for k in ["TWITTER_API_KEY","TWITTER_API_SECRET",
              "TWITTER_ACCESS_TOKEN","TWITTER_ACCESS_SECRET"]:
        if not os.environ.get(k):
            raise EnvironmentError(f"Missing: {k}")
    return tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_SECRET"])

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            return set(json.load(open(CACHE_FILE)).get("posted",[]))
        except Exception: pass
    return set()

def save_cache(posted):
    json.dump({"posted":list(posted)[-2000:],
               "updated":datetime.now(timezone.utc).isoformat()},
              open(CACHE_FILE,"w"))

def post_tweet(client, text, uid, posted, new_count, error_count):
    if uid in posted: return
    log.info(f"Posting [{uid[:12]}] len={len(text)}")
    if DRY_RUN:
        log.info(f"[DRY RUN]\n{text}\n")
        posted.add(uid); new_count[0] += 1; return
    try:
        client.create_tweet(text=text)
        posted.add(uid); new_count[0] += 1; time.sleep(2)
    except tweepy.TweepyException as e:
        log.error(f"Twitter: {e}"); error_count[0] += 1
        if "187" in str(e) or "453" in str(e): posted.add(uid)
    except Exception as e:
        log.error(f"Error: {e}"); error_count[0] += 1

def fmt_pac(dt_str):
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z","+00:00"))
        p  = dt.astimezone(PACIFIC)
        return f"{p.strftime('%I:%M %p').lstrip('0')} {'PDT' if p.dst() else 'PST'}"
    except Exception: return ""

def alert_uid(props):
    raw = (props.get("id") or props.get("@id") or
           f"{props.get('event','')}-{props.get('sent','')}-{props.get('areaDesc','')}")
    return "nws-" + hashlib.md5(raw.encode()).hexdigest()[:10]

def is_king_pierce(props):
    area  = (props.get("areaDesc") or "").upper()
    zones = {z.split("/")[-1] for z in props.get("affectedZones",[])}
    return "KING" in area or "PIERCE" in area or bool(zones & KING_PIERCE_ZONES)

def format_alert_tweet(props):
    event    = props.get("event","Weather Alert")
    emoji    = EVENT_EMOJI.get(event,"⚠️")
    area     = props.get("areaDesc","Unknown area")
    headline = (((props.get("parameters") or {}).get("NWSheadline",[""])[0])
                or props.get("headline","")
                or props.get("description","")[:120]).strip()
    exp_str  = props.get("expires") or props.get("ends") or ""
    exp_lbl  = f"Until {fmt_pac(exp_str)}" if exp_str else ""
    kp       = " 📍" if is_king_pierce(props) else ""
    marine   = " ⛵" if is_marine_alert(props) else ""
    kp       = kp or marine
    url      = props.get("@id","")
    header   = f"{emoji} {event}{kp} — {area}"
    if len(header) > 110: header = f"{emoji} {event}{kp} — {area[:85]}…"
    url_p    = f"\n{url}" if url else ""
    exp_p    = f"\n{exp_lbl}" if exp_lbl else ""
    rem      = MAX_TWEET_LEN - len(f"{header}{exp_p}\n\n{url_p}") - 2
    if headline and rem > 20:
        if len(headline) > rem: headline = headline[:rem-1]+"…"
        body = f"{header}{exp_p}\n{headline}{url_p}"
    else:
        body = f"{header}{exp_p}{url_p}"
    return body[:MAX_TWEET_LEN-1]+"…" if len(body) > MAX_TWEET_LEN else body

def fetch_alerts():
    cutoff   = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)
    features = []
    for state in STATES:
        try:
            r = requests.get(f"{NWS_ALERTS_URL}/area/{state}",
                             headers=HEADERS, timeout=20)
            r.raise_for_status()
            features.extend(r.json().get("features",[]))
        except Exception as e: log.error(f"Alert fetch ({state}): {e}")
    log.info(f"Fetched {len(features)} total active alerts.")
    recent = []
    for f in features:
        p = f.get("properties",{})
        s = p.get("sent") or p.get("effective")
        if not s: continue
        try:
            if datetime.fromisoformat(s.replace("Z","+00:00")) >= cutoff:
                recent.append(f)
        except ValueError: pass
    log.info(f"{len(recent)} alerts within lookback window.")
    return recent

def fetch_obs(stn):
    try:
        r = requests.get(NWS_OBS_URL.format(stn), headers=HEADERS, timeout=10)
        r.raise_for_status()
        p = r.json()["properties"]
        tc = p.get("temperature",{}).get("value")
        if tc is None: return None
        return {
            "temp_f":   round(tc*9/5+32,1),
            "rh":       round(p.get("relativeHumidity",{}).get("value") or 0,0),
            "wind_mph": round((p.get("windSpeed",{}).get("value") or 0)*2.237,0),
            "gust_mph": round((p.get("windGust",{}).get("value") or 0)*2.237,0),
        }
    except Exception as e:
        log.debug(f"Obs ({stn}): {e}"); return None

def fetch_obs_tweet(posted):
    now    = datetime.now(timezone.utc)
    uid    = "obs-kp-" + hashlib.md5((now.strftime("%Y%m%d")+f"_obs{now.hour//3}").encode()).hexdigest()[:8]
    if uid in posted: return None
    lines  = []
    for stn, name in [("KSEA","Seattle"),("KTIW","Tacoma"),("KRNT","Renton")]:
        obs = fetch_obs(stn)
        if obs:
            w = f"  💨{obs['wind_mph']:.0f}mph" if obs["wind_mph"] > 3 else "  calm"
            g = f"(G{obs['gust_mph']:.0f})" if obs["gust_mph"] > obs["wind_mph"]+5 else ""
            r = f"  RH:{obs['rh']:.0f}%" if obs["rh"] else ""
            lines.append(f"{name}: {obs['temp_f']:.0f}°F{r}{w}{g}")
    if not lines: return None
    p   = now.astimezone(PACIFIC)
    tz  = "PDT" if p.dst() else "PST"
    hdr = f"🌡️ King/Pierce Co. — {p.strftime('%I:%M %p').lstrip('0')} {tz}"
    txt = hdr + "\n" + "\n".join(lines) + "\n#WAwx #Seattle #Tacoma"
    return (txt[:MAX_TWEET_LEN-1]+"…" if len(txt)>MAX_TWEET_LEN else txt), uid

def check_rivers(posted):
    results = []
    for gid, info in RIVER_GAUGES.items():
        try:
            r = requests.get(NWPS_GAUGE_URL.format(gid),
                             headers={"User-Agent":HEADERS["User-Agent"]}, timeout=10)
            r.raise_for_status()
            data  = r.json()
            obs   = data.get("observed",{})
            stage = obs.get("primary",{}).get("value")
            cat   = (obs.get("floodCategory",{}).get("value","") or "").lower()
            if not stage or cat in ("","none","no flooding"): continue
            minor = (data.get("flood",{}).get("categories",{})
                        .get("minor",{}).get("stage"))
            uid   = f"gauge-{gid}-{round(stage*10)}"
            if uid in posted: continue
            txt   = (f"💧 {info['name']} at {info['city']} — {cat.title()} Flooding\n"
                     f"Stage: {stage:.1f} ft")
            if minor: txt += f" (minor flood = {minor:.1f} ft)"
            txt  += "\n#WAwx #KingCounty #PierceCounty"
            results.append((txt, uid))
        except Exception as e: log.debug(f"Gauge {gid}: {e}")
    return results

def fetch_earthquakes(posted):
    try:
        r = requests.get(USGS_EQ_URL, params={
            "format":"geojson","minmagnitude":2.5,
            "starttime":(datetime.now(timezone.utc)-timedelta(minutes=LOOKBACK_MINUTES)).isoformat(),
            "minlatitude":46.0,"maxlatitude":49.5,
            "minlongitude":-125.0,"maxlongitude":-120.0,
        }, headers={"User-Agent":HEADERS["User-Agent"]}, timeout=10)
        r.raise_for_status()
        features = r.json().get("features",[])
    except Exception as e:
        log.error(f"EQ fetch: {e}"); return []
    results = []
    for f in features:
        p   = f["properties"]
        uid = "eq-"+hashlib.md5(str(p.get("ids","")+str(p.get("time",""))).encode()).hexdigest()[:8]
        if uid in posted: continue
        mag   = p.get("mag",0)
        place = p.get("place","Puget Sound region")
        url   = p.get("url","")
        c     = f.get("geometry",{}).get("coordinates",[])
        depth = f"{c[2]:.1f}km deep" if len(c)>=3 else ""
        txt   = f"🌋 M{mag} Earthquake — {place}\n{depth}\n{url}\n#WAwx #earthquake #PNW"
        results.append((txt[:MAX_TWEET_LEN-1]+"…" if len(txt)>MAX_TWEET_LEN else txt, uid))
    return results

def fetch_sounding_tweet(posted):
    try:
        from siphon.simplewebservice.iastate import IAStateUpperAir
        import numpy as np
    except ImportError:
        log.debug("Siphon not available"); return None

    now = datetime.now(timezone.utc)
    launch_time = None
    for dh in range(25):
        t = now - timedelta(hours=dh)
        if t.hour in (0,12):
            launch_time = t.replace(minute=0,second=0,microsecond=0); break
    if launch_time is None: return None

    age_min = (now - launch_time).total_seconds()/60
    if not (30 <= age_min <= 90): return None

    uid = f"sounding-sea-{launch_time.strftime('%Y%m%d%H')}"
    if uid in posted: return None

    try:
        df = IAStateUpperAir.request_data(launch_time, "SEA")
        if df is None or len(df) < 10: return None
        p, T, Td, spd = (df["pressure"].values, df["temperature"].values,
                         df["dewpoint"].values, df["speed"].values)
        t_sfc  = float(T[0]); td_sfc = float(Td[0])
        rh_sfc = round(100*np.exp(17.625*td_sfc/(243.04+td_sfc))/
                           np.exp(17.625*t_sfc/(243.04+t_sfc)))
        i500   = int(np.argmin(np.abs(p-500)))
        i700   = int(np.argmin(np.abs(p-700)))
        t500   = float(T[i500]); t700 = float(T[i700]); td700 = float(Td[i700])
        max_w  = float(np.nanmax(spd)) if len(spd) > 0 else 0
        # PNW snow level guidance from 700mb temp
        if t700 < -2:
            snow = "  ❄️ Snow to valley floors"
        elif t700 < 0:
            snow = "  ❄️ Snow lvl low"
        elif t700 < 2:
            snow = "  🌨️ Mixed precip at passes"
        elif t700 < 5:
            snow = "  🌧️ Rain, snow above 4000ft"
        else:
            snow = ""
        pac    = launch_time.astimezone(PACIFIC)
        tz     = "PDT" if pac.dst() else "PST"
        ts     = pac.strftime("%I:%M %p").lstrip("0")
        txt    = (f"🎈 Seattle Upper Air — {ts} {tz}\n"
                  f"Sfc: {t_sfc:.1f}°C  RH:{rh_sfc}%\n"
                  f"700mb: {t700:.1f}°C  500mb: {t500:.1f}°C{snow}\n"
                  f"Max wind: {max_w:.0f}kt\n#WAwx #Seattle #Tacoma #upperair #PNW")
        return (txt[:MAX_TWEET_LEN-1]+"…" if len(txt)>MAX_TWEET_LEN else txt), uid
    except Exception as e:
        log.debug(f"Sounding: {e}"); return None

# ── Wildfire monitoring ──────────────────────────────────────────────────────
def fetch_wildfires(posted: set) -> list[tuple[str, str]]:
    """
    Fetch active NIFC fire perimeters within King/Pierce County extended bbox.
    Also catches fires approaching from eastern WA / Cascades.
    """
    import time as _time
    params = {
        "where":        ("attr_IncidentTypeCategory = 'WF' AND "
                         "(attr_PercentContained < 85 OR attr_PercentContained IS NULL)"),
        "geometry":     f"{KP_FIRE_BBOX[0]},{KP_FIRE_BBOX[1]},{KP_FIRE_BBOX[2]},{KP_FIRE_BBOX[3]}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel":   "esriSpatialRelIntersects",
        "outFields":    ("poly_IncidentName,poly_GISAcres,poly_DateCurrent,"
                         "attr_PercentContained,attr_POOCounty,attr_POOState,"
                         "attr_UniqueFireIdentifier,attr_InitialLatitude,attr_InitialLongitude"),
        "f":            "json",
    }
    results = []
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(NIFC_URL, params=params,
                             headers={"User-Agent": HEADERS["User-Agent"]},
                             timeout=30)
            r.raise_for_status()
            features = r.json().get("features", [])
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                _time.sleep(5 * (attempt + 1))
    else:
        log.error(f"NIFC fetch failed: {last_err}")
        return []

    for f in features:
        attrs = f.get("attributes", {})
        name  = attrs.get("poly_IncidentName", "Unknown Fire")
        acres = attrs.get("poly_GISAcres", 0) or 0
        pct   = attrs.get("attr_PercentContained")
        county = attrs.get("attr_POOCounty", "")
        state  = attrs.get("attr_POOState", "WA")
        uid_raw = attrs.get("attr_UniqueFireIdentifier") or f"{name}-{county}"
        uid = "nifc-" + hashlib.md5(str(uid_raw).encode()).hexdigest()[:8]
        if uid in posted or acres < FIRE_MIN_ACRES:
            continue
        pct_str = f" · {pct:.0f}% contained" if pct is not None else " · containment unknown"
        text = f"\U0001f525 Wildfire \u2014 {name}\n{acres:.0f} acres{pct_str}\n{county}, {state}\n#WAwx #wildfire #PNW"
        if len(text) <= MAX_TWEET_LEN:
            results.append((text, uid))
    log.info(f"NIFC: {len(results)} active fires in King/Pierce extended bbox.")
    return results


def fetch_inciweb(posted: set) -> list[tuple[str, str]]:
    """Fetch InciWeb RSS for WA wildfire incident updates."""
    try:
        import xml.etree.ElementTree as ET
        r = requests.get(INCIWEB_URL,
                         headers={"User-Agent": HEADERS["User-Agent"]},
                         timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)
        results = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            desc  = (item.findtext("description") or "").strip()[:100]
            pub   = item.findtext("pubDate") or ""
            # Filter to WA state
            if "Washington" not in title and "WA" not in title and "Washington" not in desc:
                continue
            uid = "inciweb-" + hashlib.md5((title+link).encode()).hexdigest()[:8]
            if uid in posted:
                continue
            # Check recency
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                if pub_dt < cutoff:
                    continue
            except Exception:
                continue
            text = f"\U0001f333\U0001f525 InciWeb \u2014 {title}\n{desc}\n{link}\n#WAwx #wildfire #Washington"
            if len(text) > MAX_TWEET_LEN:
                text = text[:MAX_TWEET_LEN-1] + "…"
            results.append((text, uid))
        log.info(f"InciWeb: {len(results)} WA incident updates.")
        return results
    except Exception as e:
        log.error(f"InciWeb fetch error: {e}")
        return []


# ── NOAA Space Weather ────────────────────────────────────────────────────────
def fetch_space_weather(posted: set) -> list[tuple[str, str]]:
    """
    NOAA SWPC geomagnetic storm alerts — relevant for PNW aurora visibility
    and potential GPS/radio disruption. Post Kp >= 5 events.
    """
    try:
        r = requests.get(SWPC_ALERT_URL,
                         headers={"User-Agent": HEADERS["User-Agent"]},
                         timeout=10)
        r.raise_for_status()
        alerts = r.json()
    except Exception as e:
        log.debug(f"SWPC fetch: {e}")
        return []

    cutoff  = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)
    results = []
    for alert in alerts:
        msg   = alert.get("message", "")
        issue = alert.get("issue_datetime", "")

        # Only post geomagnetic storm / aurora alerts
        if not any(kw in msg for kw in
                   ["Geomagnetic", "Aurora", "G1", "G2", "G3", "G4", "G5",
                    "Kp", "WATCH", "WARNING", "ALERT"]):
            continue
        # Filter to recent
        try:
            issue_dt = datetime.fromisoformat(issue.replace("Z", "+00:00"))
            if issue_dt < cutoff:
                continue
        except Exception:
            continue

        uid = "swpc-" + hashlib.md5((issue + msg[:50]).encode()).hexdigest()[:8]
        if uid in posted:
            continue

        # Extract the key line
        lines = [l.strip() for l in msg.split("\n") if l.strip()]
        summary = " ".join(lines[:3])[:200]

        text = f"\U0001f30c NOAA Space Weather Alert\n{summary}\n#aurora #PNW #WAwx #spaceweather"
        if len(text) > MAX_TWEET_LEN:
            text = text[:MAX_TWEET_LEN-1] + "…"
        results.append((text, uid))

    log.info(f"SWPC: {len(results)} space weather alerts.")
    return results


# ── Marine alerts (Puget Sound) ───────────────────────────────────────────────
def is_marine_alert(props: dict) -> bool:
    """Check if alert affects Puget Sound marine zones."""
    zones = {z.split("/")[-1] for z in props.get("affectedZones", [])}
    event = props.get("event", "")
    return bool(zones & MARINE_ZONES) or "Marine" in event or "Small Craft" in event


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    log.info("=== NWS Alert Bot (PNW Enhanced) starting ===")
    client      = None if DRY_RUN else get_twitter_client()
    posted      = load_cache()
    log.info(f"Cache contains {len(posted)} previously posted IDs.")
    new_count   = [0]; error_count = [0]

    alerts = fetch_alerts()
    kp = 0
    for f in alerts:
        props = f.get("properties",{})
        if is_king_pierce(props): kp += 1
        post_tweet(client, format_alert_tweet(props), alert_uid(props),
                   posted, new_count, error_count)
    log.info(f"NWS: {len(alerts)} alerts, {kp} King/Pierce Co.")

    for txt, uid in check_rivers(posted):
        post_tweet(client, txt, uid, posted, new_count, error_count)

    for txt, uid in fetch_earthquakes(posted):
        post_tweet(client, txt, uid, posted, new_count, error_count)

    obs = fetch_obs_tweet(posted)
    if obs:
        post_tweet(client, obs[0], obs[1], posted, new_count, error_count)
        log.info("Posted obs snapshot.")

    snd = fetch_sounding_tweet(posted)
    if snd:
        post_tweet(client, snd[0], snd[1], posted, new_count, error_count)
        log.info("Posted sounding summary.")

    save_cache(posted)
    log.info(f"Done. Posted:{new_count[0]} | Errors:{error_count[0]} | Cache:{len(posted)}")

if __name__ == "__main__":
    run()
