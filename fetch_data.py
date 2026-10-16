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
                    "title": title,
                    "link": link,
                    "summary": _teaser(e),
                    "published": pub.isoformat() if pub else None,
                }
            )
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items[: cfg.get("max_items", 60)], failed


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


def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    markets, history = fetch_markets(cfg["markets"])
    news, failed = fetch_news(cfg["news"])
    sport, sport_failed = fetch_news(cfg["sport"]) if "sport" in cfg else ([], [])

    archive = update_archive(load_previous_archive(), news, sport)
    briefing = update_briefing(cfg, markets, archive)
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = {
        "title": cfg.get("title", "Market Brief"),
        "generated_at": now_iso,
        "markets": markets,
        "news": news,
        "failed_feeds": failed,
        "sport": sport,
        "failed_sport_feeds": sport_failed,
        "ask": cfg.get("ask", {}),
        "surf": cfg.get("surf", {}),
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
    print(f"Archive: {len(archive)} headlines over the last {ARCHIVE_DAYS} days"
          f" | briefing: {briefing['date'] if briefing else 'none'}")

    ok = sum(r["ok"] for g in markets for r in g["items"])
    total = sum(len(g["items"]) for g in markets)
    print(f"Wrote {OUT.relative_to(ROOT)}: {ok}/{total} quotes, {len(news)} headlines, {len(sport)} sport")
    # Fail the run only if everything is empty, so a single outage doesn't blank the page.
    return 0 if (ok or news) else 1


if __name__ == "__main__":
    sys.exit(main())
