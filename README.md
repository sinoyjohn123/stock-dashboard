# Stock Screener — Buying Range Dashboard

Screens US (S&P 500) and India (Nifty 500) large-caps against the criteria in
your project's `screening-criteria.md`, and highlights the ones currently
sitting near their 50-day or 100-day moving average. Runs entirely on GitHub's
free infrastructure — no server, no API keys, no ongoing cost.

- `scripts/fetch_data.py` — pulls data from Yahoo Finance (via `yfinance`),
  computes the technicals/fundamentals, and writes `data/latest.json`.
- `.github/workflows/update-data.yml` — runs that script on a schedule
  (weekdays, 3x/day) and commits the refreshed data back to the repo.
- `index.html` — the dashboard itself: market toggle, sortable/filterable
  table, and a drill-down panel per stock. Reads `data/latest.json`.
- `data/latest.json` — currently **sample/placeholder data** (six stocks per
  market) so the dashboard has something to show immediately. It's overwritten
  automatically the first time the scheduled workflow runs.

## ⚠️ Important — this was built without live network access

I put this together in a sandbox that couldn't reach PyPI or Yahoo Finance, so
`fetch_data.py` could not be run end-to-end before delivery. The logic is
defensive (per-symbol error handling, retries, an offline fallback for the
India symbol list) but **the first real GitHub Actions run is the actual
first test.** If `data/latest.json` comes back empty or wrong after that run,
open the Actions log (Actions tab → the run → "Run screener" step) and send
me what it says — that's the fastest way to fix whatever Yahoo/NSE didn't
like.

## One-time setup (10–15 minutes)

1. **Create a GitHub repository.** github.com → New repository → name it
   anything (e.g. `stock-dashboard`) → **Public** (public repos get unlimited
   free Actions minutes; private repos have a monthly cap) → Create.

2. **Push these files to it.**
   ```bash
   cd stock-dashboard          # this folder
   git init
   git add .
   git commit -m "Initial dashboard"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

3. **Turn on GitHub Pages.** In the repo: Settings → Pages → under "Build and
   deployment", set Source to **Deploy from a branch**, Branch to
   **main** / **root** → Save. GitHub gives you a URL like
   `https://<your-username>.github.io/<your-repo>/` within a minute or two —
   that's your dashboard link, bookmark it.

4. **Run the data fetch once manually** (don't wait for the schedule): repo →
   Actions tab → "Update stock data" workflow → **Run workflow** → Run
   workflow. It takes a few minutes (it's checking ~1,000 candidate symbols
   across both markets). When it finishes, `data/latest.json` will have been
   committed automatically and your Pages URL will show real numbers on the
   next load.

5. That's it — after this, the workflow fires on its own schedule (see below)
   and keeps the dashboard current. No further action needed.

## Adjusting things later

- **Refresh schedule:** edit the three `cron:` lines in
  `.github/workflows/update-data.yml` (times are UTC). Or just use "Run
  workflow" any time you want an on-demand refresh.
- **Screening criteria:** edit the constants at the top of
  `scripts/fetch_data.py` — `US_MIN_MARKET_CAP`, `INDIA_MIN_MARKET_CAP_INR`,
  `BUY_RANGE_PCT`. Commit the change and the next scheduled (or manual) run
  picks it up.
- **India symbol list:** if NSE's archive CSV is unreachable from GitHub's
  runners (it happens occasionally), the script falls back to a hardcoded
  list of ~50 known Nifty large-caps in `NIFTY500_FALLBACK` inside
  `fetch_data.py`. Update that list if it goes stale.

## Local preview (optional)

`index.html` uses `fetch()`, which most browsers block on a bare
`file://` path. To preview locally:
```bash
cd stock-dashboard
python3 -m http.server 8000
# open http://localhost:8000
```

## What's *not* included

Real-time data, a dividend/news feed, alerting, and any brokerage
integration are out of scope for this build — it's a screener + dashboard
only. Also worth knowing: fundamentals (P/E, growth, debt/equity) come from
Yahoo Finance's free `.info` endpoint, which is reliable for US large-caps
but noticeably spottier for India names — a blank fundamental field for an
Indian stock usually means Yahoo just didn't have it, not that the script
failed.
