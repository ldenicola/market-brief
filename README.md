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
