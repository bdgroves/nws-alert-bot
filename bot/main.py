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
               "updated":datetime.utcnow().isoformat()},
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
        snow   = "  ❄️ Snow lvl low" if t700 < 0 else ("  🌨️ Mixed precip" if t700 < 2 else "")
        pac    = launch_time.astimezone(PACIFIC)
        tz     = "PDT" if pac.dst() else "PST"
        ts     = pac.strftime("%I:%M %p").lstrip("0")
        txt    = (f"🎈 Seattle Upper Air — {ts} {tz}\n"
                  f"Sfc: {t_sfc:.1f}°C  RH:{rh_sfc}%\n"
                  f"700mb: {t700:.1f}°C  500mb: {t500:.1f}°C{snow}\n"
                  f"Max wind: {max_w:.0f}kt\n#WAwx #Seattle #upperair #PNW")
        return (txt[:MAX_TWEET_LEN-1]+"…" if len(txt)>MAX_TWEET_LEN else txt), uid
    except Exception as e:
        log.debug(f"Sounding: {e}"); return None

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
