"""Builds site/data.json with index/currency quotes and SA news headlines.

Quotes come from Yahoo Finance via yfinance (delayed ~15 min for most
exchanges). Headlines come from the publishers' public RSS feeds; only
title, link, date and a short teaser are kept, and the page links back
to the original article.

Run locally:  python fetch_data.py   ->  writes site/data.json
"""

from __future__ import annotations

import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site" / "data.json"
SPARK_POINTS = 22  # roughly one month of trading days


def _clean(v):
    """Return a JSON-safe float or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def fetch_markets(groups: list[dict]) -> list[dict]:
    import yfinance as yf

    symbols = [it["symbol"] for g in groups for it in g["items"]]
    hist = yf.download(
        symbols,
        period="2mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )

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
                closes = hist[sym]["Close"].dropna() if len(symbols) > 1 else hist["Close"].dropna()
                if len(closes) >= 2:
                    last, prev = _clean(closes.iloc[-1]), _clean(closes.iloc[-2])
                    row.update(
                        ok=True,
                        price=last,
                        change=last - prev,
                        change_pct=(last - prev) / prev * 100 if prev else None,
                        as_of=closes.index[-1].strftime("%Y-%m-%d"),
                        spark=[_clean(x) for x in closes.iloc[-SPARK_POINTS:].tolist()],
                    )
            except Exception as e:  # one bad symbol shouldn't sink the run
                print(f"[markets] {sym}: {e}", file=sys.stderr)
            if not row["ok"]:
                print(f"[markets] no data for {sym}", file=sys.stderr)
            rows.append(row)
        out.append({"group": g["group"], "items": rows})
    return out


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


def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    markets = fetch_markets(cfg["markets"])
    news, failed = fetch_news(cfg["news"])

    payload = {
        "title": cfg.get("title", "Market Brief"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
        "news": news,
        "failed_feeds": failed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    ok = sum(r["ok"] for g in markets for r in g["items"])
    total = sum(len(g["items"]) for g in markets)
    print(f"Wrote {OUT.relative_to(ROOT)}: {ok}/{total} quotes, {len(news)} headlines")
    # Fail the run only if everything is empty, so a single outage doesn't blank the page.
    return 0 if (ok or news) else 1


if __name__ == "__main__":
    sys.exit(main())
