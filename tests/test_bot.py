"""Offline tests: fake NWS/USGS feeds and a fake X client. Run: pixi run -e dev test"""

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "bot"))

NOW = datetime.now(timezone.utc)


def iso(h):
    return (NOW + timedelta(hours=h)).isoformat()


def vt(act, phen, sig, etn, office="KSEW"):
    return f"/O.{act}.{office}.{phen}.{sig}.{etn:04d}.260101T0000Z-260102T0000Z/"


def alert(event, act="NEW", phen="WS", sig="W", etn=1, same=("053053",), ugc=("WAZ558",),
          area="Southwest Interior", office="KSEW", mtype="Alert", ends=12):
    return {"properties": {
        "id": f"{event}-{act}-{etn}-{area}", "status": "Actual", "messageType": mtype,
        "event": event, "areaDesc": area, "sent": iso(-1), "ends": iso(ends), "expires": iso(ends),
        "geocode": {"SAME": list(same), "UGC": list(ugc)},
        "parameters": {"VTEC": [vt(act, phen, sig, etn, office)] if phen else []},
        "description": "* WHAT...Heavy snow. Total accumulations of 6 to 10 inches.\n\n* WHERE...Lowlands.",
    }}


class FakeResp:
    def __init__(self, data, ok=True):
        self._d, self.ok, self.status_code = data, ok, 200

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


class FakeX:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def create_tweet(self, text):
        if self.fail:
            raise self.fail
        self.sent.append(text)


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CACHE_FILE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("DRY_RUN", raising=False)
    for m in ("core", "main"):
        sys.modules.pop(m, None)
    core = importlib.import_module("core")
    main = importlib.import_module("main")
    feeds = {"alerts": [], "quakes": []}

    def get(url, params=None, **kw):
        if "weather.gov" in url:
            return FakeResp({"features": feeds["alerts"]})
        return FakeResp({"features": feeds["quakes"]})

    import requests
    monkeypatch.setattr(requests, "get", get)
    x = FakeX()
    monkeypatch.setattr(core.Poster, "_client", lambda self: x)
    return main, core, feeds, x, tmp_path


def seed(tmp_path):
    (tmp_path / "cache.json").write_text(json.dumps({"version": 2, "seen": {}, "state": {}}))


def test_first_run_posts_nothing(bot):
    main, core, feeds, x, tmp = bot
    feeds["alerts"] = [alert("Winter Storm Warning")]
    assert main.main() == 0
    assert x.sent == []
    assert json.loads((tmp / "cache.json").read_text())["seen"]


def test_only_what_matters(bot):
    main, core, feeds, x, tmp = bot
    seed(tmp)
    feeds["alerts"] = [
        # local warning, two zones in two products -> one post
        alert("Winter Storm Warning", etn=5, area="Seattle and Vicinity", ugc=("WAZ558",)),
        alert("Winter Storm Warning", etn=5, area="Tacoma Area", ugc=("WAZ559",), same=("053053",)),
        # same hazard continuing -> nothing new
        alert("High Wind Warning", act="CON", phen="HW", etn=2),
        # local advisory and statement -> skipped
        alert("Wind Advisory", phen="WI", sig="Y", etn=3),
        alert("Special Weather Statement", phen=None, area="Southwest Interior"),
        # far-off ordinary warning -> skipped; far-off flash flood -> posted
        alert("Red Flag Warning", phen="FW", etn=9, same=("053077",), ugc=("WAZ999",),
              area="Yakima Valley", office="KPDT"),
        alert("Flash Flood Warning", phen="FF", etn=4, same=("053077",), ugc=("WAC077",),
              area="Yakima, WA", office="KPDT"),
        # cancelled -> skipped
        alert("Flood Warning", act="CAN", phen="FL", etn=7),
    ]
    assert main.main() == 0
    assert len(x.sent) == 2, x.sent
    local = x.sent[0]
    assert local.startswith("❄️ Winter Storm Warning 📍")
    assert "Seattle and Vicinity; Tacoma Area" in local
    assert "Heavy snow" in local and "weather.gov/sew/" in local
    assert x.sent[1].startswith("🌊 Flash Flood Warning\nYakima")
    assert all(len(t) <= 280 for t in x.sent)
    # second run: same feed, nothing reposts
    assert main.main() == 0
    assert len(x.sent) == 2


def test_x_failure_alerts_once(bot, monkeypatch):
    main, core, feeds, x, tmp = bot
    seed(tmp)

    class Forbidden(Exception):
        response = type("R", (), {"status_code": 403})()

    x.fail = Forbidden("403 Forbidden: client-not-enrolled")
    feeds["alerts"] = [alert("Winter Storm Warning", etn=8)]
    assert main.main() == 1          # first failure: fail the run so GitHub emails
    assert main.main() == 0          # still failing: quiet
    state = json.loads((tmp / "cache.json").read_text())
    assert state["state"]["x_ok"] is False and "403" in state["state"]["x_error"]
    assert not state["seen"]         # nothing marked, retried once X works
    x.fail = None
    assert main.main() == 0 and len(x.sent) == 1
    assert json.loads((tmp / "cache.json").read_text())["state"]["x_ok"] is True
    log = (tmp / "logs" / "posts.jsonl").read_text()
    assert "error: HTTP 403" in log and '"posted"' in log


def test_quake(bot):
    main, core, feeds, x, tmp = bot
    seed(tmp)
    feeds["quakes"] = [{"id": "uw1", "geometry": {"coordinates": [-122.3, 47.5, 22.0]},
                        "properties": {"type": "earthquake", "mag": 3.4, "place": "5 km N of Tacoma",
                                       "time": NOW.timestamp() * 1000, "felt": 41,
                                       "url": "https://earthquake.usgs.gov/x"}}]
    main.main()
    assert x.sent and x.sent[0].startswith("🌋 M3.4 earthquake — 5 km N of Tacoma")
    assert "41 felt reports" in x.sent[0]


def test_cap_holds_the_rest(bot, monkeypatch):
    main, core, feeds, x, tmp = bot
    seed(tmp)
    monkeypatch.setattr(core, "MAX_POSTS_PER_RUN", 1)
    feeds["alerts"] = [alert("Winter Storm Warning", etn=1),
                       alert("Flood Warning", phen="FL", etn=2)]
    main.main()
    assert len(x.sent) == 1
    main.main()
    assert len(x.sent) == 2
