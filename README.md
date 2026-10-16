# Market Brief

A phone-friendly page showing SA and international indices, rand exchange rates and local news headlines. It refreshes about every 15 minutes, costs nothing to run, and needs no server.

**How it works:** a GitHub Actions job runs `fetch_data.py` every 15 minutes. The script pulls delayed quotes from Yahoo Finance (via `yfinance`) and headlines from the publishers' RSS feeds, then publishes the result with the page to GitHub Pages. Your phone just opens the page.

## One-time setup (about 10 minutes)

1. **Create a repository.** On github.com, click **New repository**, name it e.g. `market-brief`, and make it **Public**. Free GitHub Pages needs a public repo; the page holds only public prices and headlines.
2. **Upload the files.** On the new repo, click **Add file → Upload files** and drag in everything from this folder, including the hidden `.github` folder. If your file browser hides it, use GitHub Desktop or `git push` instead. The workflow file must end up at `.github/workflows/update.yml`.
3. **Turn on Pages.** Go to **Settings → Pages**, and under *Build and deployment → Source* choose **GitHub Actions**.
4. **Run it once.** Go to the **Actions** tab, pick **Update Market Brief**, and click **Run workflow**. After about a minute the run shows your page address, `https://<your-username>.github.io/market-brief/`.
5. **Put it on your phone.** Open that address in Safari (iPhone) or Chrome (Android) and choose **Add to Home Screen**. It then opens full-screen like an app.

After that it updates itself on the 15-minute schedule.

## Changing what's shown

Edit `config.json`. You can do this directly on GitHub with the pencil icon, and the next run picks it up.

- **Markets:** each entry is a Yahoo Finance symbol plus a display name. To add something, find it on finance.yahoo.com and copy the symbol, for example `GC=F` for gold, `BZ=F` for Brent crude, or `^J580.JO` for an index.
- **News:** add or remove RSS feeds. Most WordPress-based sites have one at `/feed/`.

## Market detail

Tap any row on the Markets tab for a chart covering 1 month, 3 months, 6 months or 1 year. It shows the change over the period, the week's change, highs and lows, and the 52-week range. Drag a finger across the chart to read any day's closing value. The data comes from `history.json`, which each update rebuilds with about a year of daily closes.

## Daily briefing

Each morning, at the time set in `config.json` under `briefing` (06:30 by default, South African time), the update job asks Claude for a briefing. Claude works from the last day's headlines and the market moves. The briefing appears at the top of the Markets tab and stays there until the next morning.

- **Setup:** in the repository, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ANTHROPIC_API_KEY` and paste an Anthropic API key. GitHub encrypts secrets, never shows them on the public site, and hides them in run logs.
- **Generate one now:** go to **Actions → Update Market Brief → Run workflow**, tick **Generate a fresh daily briefing now**, and run it.
- **Options:** under `briefing` in `config.json`, `time` sets the hour, `weekdays_only: true` skips weekends, `enabled: false` turns it off, and `model` chooses the Claude model.
- **Cost:** one call a day, typically a few cents.
- **Timing:** GitHub sometimes starts scheduled runs late, so the briefing can appear 15–30 minutes after the set time.

## Surf tab

A surf forecast for your favourite spots, and for any beach in the world.

- **Spots:** you start with Muizenberg, Big Bay, Long Beach and Llandudno, from `surf → spots` in `config.json`. Tap **＋ Add spot** to search any beach or town worldwide, or **📍 Here** to add where you're standing. New spots are saved on your phone.
- **Rating (0–10):** combines estimated wave-face height, swell period, how directly the swell reaches the beach, wind (offshore best, onshore worst), your wave-size preference, and the spot's best tide if you set one.
- **Spot settings:** tap a spot, then **Spot settings**. The most important setting is **which way the beach faces**, because it decides which winds are offshore and which swells reach the beach. For added spots it's estimated automatically, so check it.
- **Detail:** 7 days of hourly bars, coloured by rating, with the tide curve, the best window between sunrise and sunset, and a 2-hourly table of swell, wind and tide. Drag across the chart to read any hour.
- **Map:** switch between **List** and **Map** at the top of the Surf tab. Large pins are your favourites and small pins are other Western Cape spots (`surf → library` in `config.json`). Each pin is coloured by its current rating. Tap any pin for the full report, and use **★ Add to favourites** to keep a spot. Without a key, the map uses free OpenStreetMap maps. To use **Google Maps**, open **Map settings** under the map and paste a Google Maps API key with the *Maps JavaScript API* enabled. The key is stored only on your phone. In Google Cloud Console, restrict the key to your site (`https://<your-username>.github.io/*`), because a key used in a web page is visible to anyone inspecting it. If Google rejects the key, the app switches back to the free map and tells you why. The map uses OpenStreetMap data with CARTO map tiles.
- **Swell window (`swell_from`):** for spots that receive swell from a different direction than they face, such as east-facing False Bay spots that get southwest swell wrapping around the Peninsula, set `swell_from` in the config. The beach-facing direction is still used for wind. The library's coordinates, facing directions and swell windows are reasonable estimates. Adjust them to your local knowledge.
- **Data:** live from [Open-Meteo](https://open-meteo.com/) (free for personal use, CC BY 4.0), fetched by your phone when you open the tab, so it needs no GitHub job or key. The forecasts are open-ocean models: they don't capture local sandbanks or reefs, and the tide comes from a coarse model. Use the ratings as a guide.

## Cook (in the Ask tab)

Switch the Ask tab to **Cook**.
- **Search NYT Cooking:** type a dish or ingredient and press Enter or the button. NYT Cooking's own search opens in your browser.
- **✨ Ideas from Claude:** describe what you have or feel like, for example "chicken thighs, lemons, under an hour". Claude suggests 5–6 dishes to suit the request and the season, each linking to a NYT Cooking search for that dish. Claude only suggests the dish ideas; the actual recipes always come from NYT Cooking. This uses the same Anthropic key as the news analysis.
- Your recent searches appear as chips. Sign in at cooking.nytimes.com once in your phone's browser so recipes open with your subscription.

## The Ask tab (search and AI analysis)

- **Search:** type any word or name, or tap a topic chip, to see every headline from the last 7 days that mentions it, across all News and Sport sources. Separate several terms with commas, for example `Capitec, TymeBank`.
- **Summarise & analyse:** sends the matching headlines and the market snapshot to Claude. You get back a summary, key developments, analysis and what to watch, with numbered links to the source articles.
- **API key:** the first time, open **AI settings** at the bottom of the Ask tab and paste an Anthropic API key from console.anthropic.com. The key is stored only in your phone's browser. It isn't in the repository and isn't on the public site. Set a monthly spending limit in the Anthropic Console. A typical analysis costs well under R1.
- **Topics:** edit the `ask → topics` list in `config.json`. Each topic has a label (what the chip shows) and the keywords it searches for.
- **History:** the 7-day history (`archive.json`) builds up automatically from the day you install this version. Searches cover a little less than a week until then.
- **Limits:** the analysis works from headlines and teasers, not full articles. Treat it as a well-informed briefing, and tap through to the sources for detail.

## Good to know

- **Delays.** Yahoo's index data is typically 15–20 minutes delayed. GitHub sometimes starts scheduled runs a few minutes late at busy times.
- **Inactivity pause.** GitHub pauses scheduled jobs in a repo with no commits for 60 days and emails you first. Re-enable the job with one click in the Actions tab, or make any small commit.
- **Missing quotes.** If Yahoo doesn't return a symbol, its row shows "unavailable" rather than breaking the page. If the JSE Top 40 shows that, try `^JTOPI.JO` or check the symbol on Yahoo.
- **Failed feeds.** If a news feed is down, the other feeds still show.
- **Local testing.** Run `pip install -r requirements.txt`, then `python fetch_data.py`, then `python -m http.server -d site`, and open http://localhost:8000.
