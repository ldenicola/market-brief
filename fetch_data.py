"""Builds site/data.json with index/currency quotes and SA news headlines.

Quotes come from Yahoo Finance via yfinance (delayed ~15 min for most
exchanges). Headlines come from the publishers' public RSS feeds; only
title, link, date and a short teaser are kept, and the page links back
to the original article.

It also keeps site/archive.json: a rolling 7-day collection of every
headline seen, used by the Ask tab. Each run downloads the previous
archive from the live site, adds the new headlines and drops old ones,
so no history needs to be committed to the repository.

It also writes history.json (about a year of daily closes, for the detail
chart) and, once each morning, briefing.json: a Claude-written briefing.
The briefing needs an ANTHROPIC_API_KEY secret in the GitHub repository.

Run locally:  python fetch_data.py   ->  writes the files in site/
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site" / "data.json"
ARCHIVE_OUT = ROOT / "site" / "archive.json"
SPARK_POINTS = 22  # roughly one month of trading days
ARCHIVE_DAYS = 7
ARCHIVE_MAX = 2500


def _clean(v):
    """Return a JSON-safe float or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def fetch_markets(groups: list[dict]) -> tuple[list[dict], dict]:
    """Returns (rows grouped for display, {symbol: {"d": dates, "c": closes}} for ~1 year).

    Each item may list "fallbacks": other symbols to use if Yahoo has no current
    data for the main one (e.g. a Satrix ETF that tracks a JSE sector index)."""
    import yfinance as yf

    def candidates(it):
        return [it["symbol"], *it.get("fallbacks", [])]

    symbols = list(dict.fromkeys(s for g in groups for it in g["items"] for s in candidates(it)))
    history: dict[str, dict] = {}
    hist = yf.download(
        symbols,
        period="1y",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    stale_before = datetime.now(timezone.utc).date() - timedelta(days=10)

    def closes_for(sym):
        try:
            c = hist[sym]["Close"].dropna() if len(symbols) > 1 else hist["Close"].dropna()
        except Exception:
            return None
        if len(c) < 2 or c.index[-1].date() < stale_before:  # missing or no longer updated
            return None
        return c

    out = []
    for g in groups:
        rows = []
        for it in g["items"]:
            sym = it["symbol"]
            row = {
                "symbol": sym,
                "name": it["name"],
                "decimals": it.get("decimals", 2),
                "ok": False,
            }
            try:
                used, closes = next(((c, cl) for c in candidates(it) if (cl := closes_for(c)) is not None), (None, None))
                if closes is not None:
                    last, prev = _clean(closes.iloc[-1]), _clean(closes.iloc[-2])
                    nd = it.get("decimals", 2) + 2
                    history[sym] = {
                        "d": [ix.strftime("%Y-%m-%d") for ix in closes.index],
                        "c": [round(float(x), nd) for x in closes.tolist()],
                    }
                    week_ago = _clean(closes.iloc[-6]) if len(closes) >= 6 else None
                    row.update(
                        week_pct=(last - week_ago) / week_ago * 100 if week_ago else None,
                        ok=True,
                        price=last,
                        change=last - prev,
                        change_pct=(last - prev) / prev * 100 if prev else None,
                        as_of=closes.index[-1].strftime("%Y-%m-%d"),
                        spark=[_clean(x) for x in closes.iloc[-SPARK_POINTS:].tolist()],
                    )
                    if used != sym:
                        row["source_symbol"] = used
                        if used.endswith(".JO") and not used.startswith("^"):
                            row["proxy"] = "ETF"  # JSE ETF prices are quoted in cents
                        print(f"[markets] {sym}: using fallback {used}", file=sys.stderr)
            except Exception as e:  # one bad symbol shouldn't sink the run
                print(f"[markets] {sym}: {e}", file=sys.stderr)
            if not row["ok"]:
                print(f"[markets] no data for {sym} (tried {', '.join(candidates(it))})", file=sys.stderr)
            rows.append(row)
        out.append({"group": g["group"], "items": rows})
    return out, history


_TAG = re.compile(r"<[^>]+>")


def _teaser(entry, limit=180) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    text = html.unescape(_TAG.sub(" ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    # many WordPress feeds append "The post X appeared first on Y."
    text = re.sub(r"\s*The post .* appeared first on .*$", "", text)
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _published(entry) -> datetime | None:
    for key in ("published", "updated", "pubDate"):
        if entry.get(key):
            try:
                d = parsedate_to_datetime(entry[key])
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    for key in ("published_parsed", "updated_parsed"):
        if entry.get(key):
            return datetime(*entry[key][:6], tzinfo=timezone.utc)
    return None


def fetch_news(cfg: dict) -> tuple[list[dict], list[str]]:
    import feedparser

    items, seen, failed = [], set(), []
    for feed in cfg["feeds"]:
        parsed = feedparser.parse(
            feed["url"],
            agent="Mozilla/5.0 (personal news reader; +https://github.com)",
        )
        if not parsed.entries:
            failed.append(feed["name"])
            print(f"[news] {feed['name']}: no entries ({parsed.get('bozo_exception', '')})", file=sys.stderr)
            continue
        for e in parsed.entries[:30]:
            link, title = e.get("link"), html.unescape(e.get("title", "")).strip()
            if not link or not title:
                continue
            key = re.sub(r"\W+", "", title.lower())[:80]
            if key in seen:
                continue
            seen.add(key)
            pub = _published(e)
            items.append(
                {
                    "source": feed["name"],
                    "region": feed.get("region", "sa"),
                    "title": title,
                    "link": link,
                    "summary": _teaser(e),
                    "published": pub.isoformat() if pub else None,
                }
            )
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    # Report a source as failed only if all its sections failed; otherwise say how many did.
    total = {}
    for f in cfg["feeds"]:
        total[f["name"]] = total.get(f["name"], 0) + 1
    summary = []
    for name in dict.fromkeys(failed):
        n = failed.count(name)
        summary.append(name if n == total[name] else f"{name} ({n} of {total[name]} sections)")
    return items[: cfg.get("max_items", 60)], summary


def _site_url() -> str | None:
    """Public address of this site, e.g. https://name.github.io/market-brief/."""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY")  # set automatically in GitHub Actions
    if not repo or "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def load_previous(name: str):
    """Download a file this job published last time (the live site is the only storage)."""
    base = _site_url()
    if not base:
        return None
    try:
        req = urllib.request.Request(
            base + name + f"?t={int(datetime.now().timestamp())}",
            headers={"User-Agent": "market-brief-updater", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # first run, or the site is briefly unavailable
        print(f"[{name}] no previous copy ({e})", file=sys.stderr)
        return None


def load_previous_archive() -> list[dict]:
    data = load_previous("archive.json")
    return data.get("items", []) if isinstance(data, dict) else []


# ---------------------------------------------------------------- daily briefing

def _sa_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Africa/Johannesburg"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=2)))


BRIEFING_SYSTEM = """You write a short morning briefing for a South African management consultant who works mainly with asset managers, unit trust companies and other financial services firms. He reads it on his phone first thing in the morning.

You receive a market snapshot and a numbered list of headlines (titles and short teasers only) from the last day. Work only from this material; don't present outside knowledge as news. Brief, clearly flagged background is fine where it helps.

Use exactly these headings, in this order:
### In brief
Three or four sentences: the handful of things that matter most today.
### Markets
How the JSE, rand, global indices, gold, oil and Bitcoin moved and why, as far as the headlines explain it. Mention notable daily or weekly moves with figures.
### South Africa
Bullets on the most important local economic, political and policy stories.
### Financial services
Bullets on anything relevant to asset managers, unit trusts, banks, insurers, regulators (SARB, FSCA, National Treasury) or retirement funds. If nothing relevant, say so in one line.
### Global
Bullets on the international stories that matter for South African markets and investors.
### What to watch
Two to four bullets: decisions, data or events coming up.
### Sport
One or two bullets on the biggest sports stories, especially South African teams. Skip this heading if there's nothing notable.

Rules: cite the headlines you use with their numbers in square brackets, e.g. [4] or [2][9]. Be concise and specific, about 350–500 words in total. Plain British/South African English. Only "### " headings, "- " bullets and **bold**; no tables. If the news is thin, say so briefly rather than padding."""


def _fmt_num(v, d=2):
    return "–" if v is None else f"{v:,.{d}f}"


def build_briefing_prompt(markets: list[dict], archive: list[dict], now_sa: datetime) -> tuple[str, list[dict]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=26)

    def recent(it):
        try:
            return datetime.fromisoformat(it.get("published") or it.get("first_seen")) >= cutoff
        except (TypeError, ValueError):
            return False

    news = [it for it in archive if it.get("kind") != "sport" and recent(it)][:220]
    sport = [it for it in archive if it.get("kind") == "sport" and recent(it)][:25]
    refs = news + sport

    lines = []
    for g in markets:
        for q in g["items"]:
            if q.get("ok"):
                wk = f", {q['week_pct']:+.2f}% over the week" if q.get("week_pct") is not None else ""
                proxy = " (via tracking ETF, price in cents; use the % moves)" if q.get("proxy") else ""
                lines.append(f"{q['name']}{proxy}: {_fmt_num(q['price'], q.get('decimals', 2))} "
                             f"({q['change_pct']:+.2f}% on the day{wk}; as of {q['as_of']})")

    def fmt(i, it):
        return f"[{i}] {it['source']}: {it['title']}" + (f" — {it['summary']}" if it.get("summary") else "")

    heads = "\n".join(fmt(i + 1, it) for i, it in enumerate(refs))
    prompt = (f"Today is {now_sa.strftime('%A %d %B %Y')}, {now_sa.strftime('%H:%M')} in South Africa.\n\n"
              f"Market snapshot (latest close, delayed):\n" + "\n".join(lines) +
              f"\n\nHeadlines from roughly the last 24 hours (news first, then sport):\n{heads}")
    return prompt, refs


def call_claude(api_key: str, model: str, system: str, prompt: str, max_tokens: int = 1800) -> str:
    body = json.dumps({
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"),
        data=body, method="POST",
        headers={"content-type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text").strip()


def update_briefing(cfg: dict, markets: list[dict], archive: list[dict]) -> dict | None:
    """Keep yesterday's briefing until it's time for today's, then generate a new one."""
    bcfg = cfg.get("briefing", {})
    previous = load_previous("briefing.json")
    if not bcfg.get("enabled", True):
        return None

    now_sa = _sa_now()
    today = now_sa.strftime("%Y-%m-%d")
    force = os.environ.get("FORCE_BRIEFING", "").lower() == "true"
    hh, mm = (int(x) for x in str(bcfg.get("time", "06:30")).split(":"))
    due = (now_sa.hour, now_sa.minute) >= (hh, mm)
    weekday_ok = now_sa.weekday() < 5 or not bcfg.get("weekdays_only", False)
    have_today = isinstance(previous, dict) and previous.get("date") == today

    if not force and (have_today or not due or not weekday_ok):
        return previous if isinstance(previous, dict) else None

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("[briefing] skipped: no ANTHROPIC_API_KEY secret set", file=sys.stderr)
        return previous if isinstance(previous, dict) else None

    prompt, refs = build_briefing_prompt(markets, archive, now_sa)
    if not refs:
        print("[briefing] skipped: no recent headlines", file=sys.stderr)
        return previous if isinstance(previous, dict) else None
    try:
        text = call_claude(key, bcfg.get("model") or cfg.get("ask", {}).get("model") or "claude-sonnet-5-5",
                           BRIEFING_SYSTEM, prompt)
    except Exception as e:  # keep the old briefing rather than failing the whole update
        detail = e.read().decode("utf-8", "ignore")[:300] if hasattr(e, "read") else ""
        print(f"[briefing] generation failed: {e} {detail}", file=sys.stderr)
        return previous if isinstance(previous, dict) else None

    print(f"[briefing] generated from {len(refs)} headlines")
    return {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "refs": [{"source": r["source"], "title": r["title"], "link": r["link"]} for r in refs],
    }


def update_archive(previous: list[dict], news: list[dict], sport: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ARCHIVE_DAYS)
    by_link: dict[str, dict] = {}
    for it in previous:
        if it.get("link"):
            by_link[it["link"]] = it
    for kind, items in (("news", news), ("sport", sport)):
        for it in items:
            old = by_link.get(it["link"], {})
            by_link[it["link"]] = {
                **it,
                "kind": kind,
                "first_seen": old.get("first_seen") or now.isoformat(),
            }

    def when(it):
        try:
            return datetime.fromisoformat(it.get("published") or it.get("first_seen"))
        except (TypeError, ValueError):
            return now

    kept = [it for it in by_link.values() if when(it) >= cutoff]
    kept.sort(key=when, reverse=True)
    return kept[:ARCHIVE_MAX]


# ---------------------------------------------------------------- beach water quality (City of Cape Town)

BEACH_API = "https://beachalerts.capetown.gov.za/api"
BEACH_MIN_AGE = timedelta(minutes=55)   # the City updates twice a day on weekdays; be gentle
BEACH_FIELDS = ("id", "name", "code", "latitude", "longitude", "coastline", "status", "enterococci",
                "lastSampleDate", "shortTermFlag", "longTermFlag", "currentStatus", "detailUrl")
ALERT_FIELDS = ("key", "title", "category", "subtype", "message", "activeFrom", "activeUntil",
                "resolutionNote", "seawaterState", "sandState", "coastState", "affectedBeachCodes")


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Markets personal app (GitHub Pages)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def update_beaches(cfg: dict) -> dict | None:
    """Coastal water quality for Cape Town beaches, from the City's Beach Alerts site.
    Reuses the last published copy if it's less than an hour old or the City's site is unavailable."""
    if not cfg.get("water", {}).get("enabled", True):
        return None
    previous = load_previous("beaches.json")
    now = datetime.now(timezone.utc)
    if isinstance(previous, dict) and previous.get("fetched_at"):
        try:
            if now - datetime.fromisoformat(previous["fetched_at"]) < BEACH_MIN_AGE:
                return previous
        except ValueError:
            pass
    try:
        b = _get_json(BEACH_API + "/beaches")
        beaches = b.get("beaches", b) if isinstance(b, dict) else b
        try:
            alerts = _get_json(BEACH_API + "/alerts/active")
        except Exception as e:
            print(f"[beaches] alerts unavailable ({e})", file=sys.stderr)
            alerts = (previous or {}).get("alerts", [])
        out = {
            "fetched_at": now.isoformat(),
            "source_updated": b.get("lastUpdated") if isinstance(b, dict) else None,
            "beaches": [{k: x.get(k) for k in BEACH_FIELDS} for x in beaches if x.get("latitude") and x.get("longitude")],
            "alerts": [{k: a.get(k) for k in ALERT_FIELDS} for a in (alerts if isinstance(alerts, list) else [])],
        }
        print(f"[beaches] {len(out['beaches'])} beaches, {len(out['alerts'])} active alerts")
        return out
    except Exception as e:
        print(f"[beaches] fetch failed, keeping previous ({e})", file=sys.stderr)
        return previous if isinstance(previous, dict) else None


# ---------------------------------------------------------------- surf (same model as the app's Surf tab)

SURF_MIN_AGE = timedelta(minutes=55)
SIZE_PREFS = {"small": (0.5, 1.2), "medium": (0.9, 2.0), "big": (1.5, 3.5)}
RATING_BANDS = [(1.5, "Poor"), (3, "Poor"), (4.5, "Poor-fair"), (6, "Fair"), (7.5, "Good"), (9, "Very good"), (99, "Epic")]
DIRS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
MARINE_FULL = ("wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_direction,swell_wave_period,"
               "secondary_swell_wave_height,secondary_swell_wave_direction,secondary_swell_wave_period,"
               "wind_wave_height,sea_level_height_msl,sea_surface_temperature")
MARINE_CORE = "wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_direction,swell_wave_period,wind_wave_height"
FT = 3.281


def _ang(a, b):
    return abs(((a - b) % 360 + 540) % 360 - 180)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _lerp(x, x0, x1, y0, y1):
    return y0 if x <= x0 else y1 if x >= x1 else y0 + (x - x0) / (x1 - x0) * (y1 - y0)


def _dir(d):
    return "-" if d is None else DIRS16[round((d % 360) / 22.5) % 16]


def _band(r):
    return next(lbl for lim, lbl in RATING_BANDS if r < lim)


def _surf_range(h):
    if h < 0.25:
        return "flat"
    lo = max(1, round(h * 0.8 * FT)); hi = max(lo, round(h * 1.15 * FT))
    return f"{lo} ft" if lo == hi else f"{lo}-{hi} ft"


def surf_forecast(spot: dict, m: dict, w: dict, size: str) -> dict:
    """Python port of buildForecast() in site/index.html: hourly surf height, wind quality, tide and a 0-10 rating."""
    H, W = m["hourly"], w["hourly"]
    widx = {t: i for i, t in enumerate(W["time"])}
    pref = SIZE_PREFS.get(size, SIZE_PREFS["medium"])
    g = lambda k, i: (H.get(k) or [None] * (i + 1))[i] if H.get(k) and i < len(H[k]) else None
    hours = []
    for i, t in enumerate(H["time"]):
        trains = [(g(a, i), g(b, i), g(c, i)) for a, b, c in (
            ("swell_wave_height", "swell_wave_period", "swell_wave_direction"),
            ("secondary_swell_wave_height", "secondary_swell_wave_period", "secondary_swell_wave_direction"))]
        trains = [x for x in trains if x[0] is not None and x[0] > 0.05 and x[1] is not None and x[2] is not None]
        if not trains and g("wave_height", i) is not None:
            trains = [(g("wave_height", i), g("wave_period", i) or 8, g("wave_direction", i) if g("wave_direction", i) is not None else spot["facing"])]
        e2, best, best_face = 0.0, None, -1
        for h, T, d in trains:
            exposure = _clamp(1 - (_ang(d, spot.get("swell_from", spot["facing"])) - 40) / 70, 0, 1) ** 1.3
            face = h * _clamp(0.55 + T * 0.055, 0.8, 1.55) * exposure
            e2 += face * face
            if face > best_face:
                best_face, best = face, (h, T, d)
        surf_h = math.sqrt(e2 + ((g("wind_wave_height", i) or 0) * 0.4) ** 2)
        wi = widx.get(t)
        ws = W["wind_speed_10m"][wi] if wi is not None else None
        wd = W["wind_direction_10m"][wi] if wi is not None else None
        kind, wscore = "unknown", 0.6
        if ws is not None and wd is not None:
            off = _ang(wd, (spot["facing"] + 180) % 360)
            if off <= 45:
                kind, wscore = "offshore", 1 if ws <= 25 else _lerp(ws, 25, 45, 1, 0.55)
            elif off <= 80:
                kind, wscore = "cross-offshore", _lerp(ws, 12, 30, 0.95, 0.3)
            elif off <= 110:
                kind, wscore = "cross-shore", _lerp(ws, 8, 22, 0.85, 0.15)
            else:
                kind, wscore = "onshore", _lerp(ws, 6, 18, 0.75, 0.08)
            if ws < 6:
                kind = "light"
        size_s = (_lerp(surf_h, pref[0] * 0.35, pref[0], 0, 1) if surf_h < pref[0]
                  else _lerp(surf_h, pref[1], pref[1] * 1.9, 1, 0.1) if surf_h > pref[1] else 1)
        period_q = _clamp((best[1] - 5) / 8, 0.15, 1) if best else 0.3
        hours.append({"t": t, "surf_h": surf_h, "best": best, "ws": ws, "wd": wd, "kind": kind, "wscore": wscore,
                      "size_s": size_s, "period_q": period_q, "sea": g("sea_level_height_msl", i), "sst": g("sea_surface_temperature", i)})
    by_day = {}
    for i, h in enumerate(hours):
        by_day.setdefault(h["t"][:10], []).append(i)
    for idx in by_day.values():
        lv = [hours[i]["sea"] for i in idx if hours[i]["sea"] is not None]
        lo, hi = (min(lv), max(lv)) if lv else (0, 0)
        for i in idx:
            h = hours[i]
            h["tide_pos"] = (h["sea"] - lo) / (hi - lo) if h["sea"] is not None and hi - lo > 0.05 else None
            nx = hours[i + 1] if i + 1 < len(hours) else None
            h["tide_dir"] = (None if h["sea"] is None or not nx or nx["sea"] is None else
                             "rising" if nx["sea"] > h["sea"] + 0.005 else "falling" if nx["sea"] < h["sea"] - 0.005 else "turning")
    target = {"low": 0.15, "mid": 0.5, "high": 0.85}.get(spot.get("tide"))
    for h in hours:
        tide_f = 1 if target is None or h["tide_pos"] is None else 1 - 0.5 * min(1, abs(h["tide_pos"] - target) / 0.6)
        r = 10 * h["size_s"] ** 0.9 * (0.4 + 0.6 * h["wscore"]) * (0.65 + 0.35 * h["period_q"]) * tide_f
        if h["surf_h"] < 0.25:
            r = min(r, 1)
        h["rating"] = round(r, 1)
        h["label"] = ("Flat" if h["surf_h"] < 0.25 else "Too big" if h["rating"] < 3 and h["size_s"] < 0.6 and h["surf_h"] > pref[1]
                      else "Too small" if h["rating"] < 3 and h["size_s"] < 0.6 else _band(h["rating"]))
    days = [{"date": d, "sunrise": w["daily"]["sunrise"][i], "sunset": w["daily"]["sunset"][i]} for i, d in enumerate(w.get("daily", {}).get("time", []))]
    for h in hours:
        d = next((x for x in days if x["date"] == h["t"][:10]), None)
        h["daylight"] = not d or (h["t"] >= d["sunrise"][:13] and h["t"] <= d["sunset"])

    def tide_txt(h):
        if h["tide_pos"] is None:
            return None
        lvl = "low" if h["tide_pos"] < 0.33 else "high" if h["tide_pos"] > 0.67 else "mid"
        return lvl + (", " + h["tide_dir"] if h["tide_dir"] else "")

    def best_window(date, start_i=0):
        idx = [i for i, h in enumerate(hours) if h["t"].startswith(date) and h["daylight"] and i >= start_i]
        if not idx:
            return None
        top = max(idx, key=lambda i: hours[i]["rating"])
        s_, e_ = top, top
        ok = lambda i: i in idx and hours[i]["rating"] >= hours[top]["rating"] - 1
        while ok(s_ - 1): s_ -= 1
        while ok(e_ + 1): e_ += 1
        end = hours[min(e_ + 1, len(hours) - 1)]["t"]
        d = next((x for x in days if x["date"] == date), None)
        if d and (end > d["sunset"] or not end.startswith(date)):
            end = d["sunset"]
        return {"from": hours[s_]["t"][11:16], "to": end[11:16], "rating": hours[top]["rating"], "label": hours[top]["label"]}

    def slim(h):
        b = h["best"]
        return {"time": h["t"], "rating": h["rating"], "label": h["label"], "surf": _surf_range(h["surf_h"]),
                "swell": f"{max(1, round(b[0] * FT))} ft @ {round(b[1])}s {_dir(b[2])}" if b else None,
                "wind": None if h["ws"] is None else f"{round(h['ws'] / 3.6)} m/s {_dir(h['wd'])} ({h['kind']})",
                "tide": tide_txt(h)}

    offset = m.get("utc_offset_seconds") or w.get("utc_offset_seconds") or 0
    local_now = (datetime.now(timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H")
    now_i = next((i for i, h in enumerate(hours) if h["t"][:13] == local_now), 0)
    out_days = []
    for d in days[:4]:
        start = now_i if d["date"] == hours[now_i]["t"][:10] else 0
        out_days.append({"date": d["date"], "best": best_window(d["date"], start),
                         "hours": [slim(h) for i, h in enumerate(hours)
                                   if h["t"].startswith(d["date"]) and h["daylight"] and i >= start and int(h["t"][11:13]) % 2 == 0]})
    sst = hours[now_i]["sst"]
    return {"id": spot["id"], "name": spot["name"], "place": spot.get("place", ""), "lat": spot["lat"], "lon": spot["lon"],
            "facing": _dir(spot["facing"]), "now": slim(hours[now_i]), "water_temp_c": None if sst is None else round(sst),
            "days": out_days}


def update_surf(cfg: dict) -> dict | None:
    """Surf ratings for every configured spot (favourites + library), for Dominic and other readers.
    The phone app still computes its own live forecast; this mirrors it hourly."""
    scfg = cfg.get("surf", {})
    spots = [dict(x, favourite=True) for x in scfg.get("spots", [])] + [dict(x, favourite=False) for x in scfg.get("library", [])]
    if not spots:
        return None
    previous = load_previous("surf.json")
    now = datetime.now(timezone.utc)
    if isinstance(previous, dict) and previous.get("fetched_at"):
        try:
            if now - datetime.fromisoformat(previous["fetched_at"]) < SURF_MIN_AGE:
                return previous
        except ValueError:
            pass
    lat = ",".join(str(x["lat"]) for x in spots); lon = ",".join(str(x["lon"]) for x in spots)
    base = f"latitude={lat}&longitude={lon}&timezone=auto&forecast_days=4"
    as_list = lambda j: j if isinstance(j, list) else [j]
    try:
        try:
            marine = as_list(_get_json(f"https://marine-api.open-meteo.com/v1/marine?{base}&cell_selection=sea&hourly={MARINE_FULL}"))
        except Exception:
            marine = as_list(_get_json(f"https://marine-api.open-meteo.com/v1/marine?{base}&hourly={MARINE_CORE}"))
        weather = as_list(_get_json(f"https://api.open-meteo.com/v1/forecast?{base}&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m&daily=sunrise,sunset&wind_speed_unit=kmh"))
        size = scfg.get("size", "medium")
        out = []
        for sp, mm, ww in zip(spots, marine, weather):
            try:
                f = surf_forecast(sp, mm, ww, size); f["favourite"] = sp["favourite"]; out.append(f)
            except Exception as e:
                print(f"[surf] {sp['name']}: {e}", file=sys.stderr)
        print(f"[surf] {len(out)} spots forecast")
        return {"fetched_at": now.isoformat(), "size_preference": size, "source": "Open-Meteo.com (CC BY 4.0)", "spots": out}
    except Exception as e:
        print(f"[surf] forecast failed, keeping previous ({e})", file=sys.stderr)
        return previous if isinstance(previous, dict) else None


def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    markets, history = fetch_markets(cfg["markets"])
    news, failed = fetch_news(cfg["news"])
    sport, sport_failed = fetch_news(cfg["sport"]) if "sport" in cfg else ([], [])

    archive = update_archive(load_previous_archive(), news, sport)
    briefing = update_briefing(cfg, markets, archive)
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = {
        "title": cfg.get("title", "Markets"),
        "generated_at": now_iso,
        "markets": markets,
        "news": news,
        "failed_feeds": failed,
        "feed_regions": {f["name"]: f.get("region", "sa") for f in cfg["news"]["feeds"]},
        "sport": sport,
        "failed_sport_feeds": sport_failed,
        "ask": cfg.get("ask", {}),
        "surf": cfg.get("surf", {}),
        "water": cfg.get("water", {}),
        "briefing": briefing,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)

    def write(path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    write(OUT, payload)
    write(ARCHIVE_OUT, {"generated_at": now_iso, "days": ARCHIVE_DAYS, "items": archive})
    write(ROOT / "site" / "history.json", {"generated_at": now_iso, "series": history})
    if briefing:
        write(ROOT / "site" / "briefing.json", briefing)
    beaches = update_beaches(cfg)
    if beaches:
        write(ROOT / "site" / "beaches.json", beaches)
    surf = update_surf(cfg)
    if surf:
        write(ROOT / "site" / "surf.json", surf)
    print(f"Archive: {len(archive)} headlines over the last {ARCHIVE_DAYS} days"
          f" | briefing: {briefing['date'] if briefing else 'none'}")

    ok = sum(r["ok"] for g in markets for r in g["items"])
    total = sum(len(g["items"]) for g in markets)
    print(f"Wrote {OUT.relative_to(ROOT)}: {ok}/{total} quotes, {len(news)} headlines, {len(sport)} sport")
    # Fail the run only if everything is empty, so a single outage doesn't blank the page.
    return 0 if (ok or news) else 1


if __name__ == "__main__":
    sys.exit(main())
