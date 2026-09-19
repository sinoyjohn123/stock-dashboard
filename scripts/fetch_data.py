#!/usr/bin/env python3
"""
Fetch US + India large-cap stock data, compute technicals/fundamentals,
flag "buying range" stocks, and write data/latest.json for the dashboard.

Data source: Yahoo Finance via the `yfinance` package. No API keys needed.
Designed to run on GitHub Actions — see .github/workflows/update-data.yml.

Criteria (kept in sync with the project's screening-criteria.md):
  - US universe:    S&P 500 constituents, market cap >= $50B
  - India universe: Nifty 500 constituents, market cap >= INR 50,000 crore
  - Buying range:   current price within BUY_RANGE_PCT of the 50-day
                     or 100-day simple moving average (either qualifies)

NOTE ON RELIABILITY: this script was written and packaged in an environment
with no outbound access to pypi.org or Yahoo Finance, so it could not be
executed end-to-end before delivery. The logic has been kept deliberately
defensive (per-symbol try/except, retries, fallback lists) so a handful of
bad tickers or a flaky endpoint won't kill the whole run — but the FIRST
real run on GitHub Actions is effectively the first real test. Check the
Actions run log if data/latest.json looks wrong or empty.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

# ---------------------------------------------------------------------------
# Config — edit these to change the screening criteria
# ---------------------------------------------------------------------------
US_MIN_MARKET_CAP = 50_000_000_000        # $50B
INDIA_MIN_MARKET_CAP_INR = 50_000 * 1e7   # INR 50,000 crore (1 crore = 1e7)
BUY_RANGE_PCT = 0.03                      # within 3% of SMA50 or SMA100
REQUEST_SLEEP_SEC = 0.4                   # politeness delay between symbols
OUTPUT_PATH = "data/latest.json"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Safety-net list used only if the live NSE archive fetch fails (NSE is known
# to occasionally 403 non-browser / cloud-IP requests). These are major NSE
# large caps — comfortably above the INR 50,000 crore floor as of 2026, but
# re-check membership occasionally since it will go stale over time.
NIFTY500_FALLBACK = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Energy"},
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT"},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Financials"},
    {"symbol": "ICICIBANK", "name": "ICICI Bank", "sector": "Financials"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "sector": "Telecom"},
    {"symbol": "SBIN", "name": "State Bank of India", "sector": "Financials"},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT"},
    {"symbol": "LICI", "name": "Life Insurance Corporation of India", "sector": "Financials"},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever", "sector": "Consumer Staples"},
    {"symbol": "ITC", "name": "ITC", "sector": "Consumer Staples"},
    {"symbol": "LT", "name": "Larsen & Toubro", "sector": "Industrials"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "sector": "Financials"},
    {"symbol": "HCLTECH", "name": "HCL Technologies", "sector": "IT"},
    {"symbol": "MARUTI", "name": "Maruti Suzuki", "sector": "Consumer Discretionary"},
    {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical", "sector": "Healthcare"},
    {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank", "sector": "Financials"},
    {"symbol": "TITAN", "name": "Titan Company", "sector": "Consumer Discretionary"},
    {"symbol": "AXISBANK", "name": "Axis Bank", "sector": "Financials"},
    {"symbol": "NTPC", "name": "NTPC", "sector": "Utilities"},
    {"symbol": "ADANIENT", "name": "Adani Enterprises", "sector": "Diversified"},
    {"symbol": "ADANIPORTS", "name": "Adani Ports", "sector": "Industrials"},
    {"symbol": "ULTRACEMCO", "name": "UltraTech Cement", "sector": "Materials"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints", "sector": "Materials"},
    {"symbol": "WIPRO", "name": "Wipro", "sector": "IT"},
    {"symbol": "ONGC", "name": "Oil & Natural Gas Corp", "sector": "Energy"},
    {"symbol": "COALINDIA", "name": "Coal India", "sector": "Energy"},
    {"symbol": "POWERGRID", "name": "Power Grid Corp", "sector": "Utilities"},
    {"symbol": "TATAMOTORS", "name": "Tata Motors", "sector": "Consumer Discretionary"},
    {"symbol": "JSWSTEEL", "name": "JSW Steel", "sector": "Materials"},
    {"symbol": "NESTLEIND", "name": "Nestle India", "sector": "Consumer Staples"},
    {"symbol": "BAJAJ-AUTO", "name": "Bajaj Auto", "sector": "Consumer Discretionary"},
    {"symbol": "INDUSINDBK", "name": "IndusInd Bank", "sector": "Financials"},
    {"symbol": "TECHM", "name": "Tech Mahindra", "sector": "IT"},
    {"symbol": "GRASIM", "name": "Grasim Industries", "sector": "Materials"},
    {"symbol": "M&M", "name": "Mahindra & Mahindra", "sector": "Consumer Discretionary"},
    {"symbol": "TATASTEEL", "name": "Tata Steel", "sector": "Materials"},
    {"symbol": "HDFCLIFE", "name": "HDFC Life Insurance", "sector": "Financials"},
    {"symbol": "SBILIFE", "name": "SBI Life Insurance", "sector": "Financials"},
    {"symbol": "CIPLA", "name": "Cipla", "sector": "Healthcare"},
    {"symbol": "DRREDDY", "name": "Dr. Reddy's Laboratories", "sector": "Healthcare"},
    {"symbol": "EICHERMOT", "name": "Eicher Motors", "sector": "Consumer Discretionary"},
    {"symbol": "BPCL", "name": "Bharat Petroleum", "sector": "Energy"},
    {"symbol": "BRITANNIA", "name": "Britannia Industries", "sector": "Consumer Staples"},
    {"symbol": "DIVISLAB", "name": "Divi's Laboratories", "sector": "Healthcare"},
    {"symbol": "APOLLOHOSP", "name": "Apollo Hospitals", "sector": "Healthcare"},
    {"symbol": "HEROMOTOCO", "name": "Hero MotoCorp", "sector": "Consumer Discretionary"},
    {"symbol": "ADANIGREEN", "name": "Adani Green Energy", "sector": "Utilities"},
    {"symbol": "VEDL", "name": "Vedanta", "sector": "Materials"},
    {"symbol": "BANKBARODA", "name": "Bank of Baroda", "sector": "Financials"},
    {"symbol": "PIDILITIND", "name": "Pidilite Industries", "sector": "Materials"},
    {"symbol": "SIEMENS", "name": "Siemens India", "sector": "Industrials"},
    {"symbol": "AMBUJACEM", "name": "Ambuja Cements", "sector": "Materials"},
    {"symbol": "TRENT", "name": "Trent", "sector": "Consumer Discretionary"},
    {"symbol": "VBL", "name": "Varun Beverages", "sector": "Consumer Staples"},
]


# ---------------------------------------------------------------------------
# Universe builders
# ---------------------------------------------------------------------------
# Safety-net list used only if the Wikipedia S&P 500 fetch fails. These are
# major US large caps — comfortably above the $50B floor as of 2026, but
# re-check periodically since membership will go stale over time.
SP500_FALLBACK = [
    {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Information Technology"},
    {"symbol": "MSFT", "name": "Microsoft Corporation", "sector": "Information Technology"},
    {"symbol": "GOOGL", "name": "Alphabet Inc.", "sector": "Communication Services"},
    {"symbol": "AMZN", "name": "Amazon.com Inc.", "sector": "Consumer Discretionary"},
    {"symbol": "NVDA", "name": "NVIDIA Corporation", "sector": "Information Technology"},
    {"symbol": "META", "name": "Meta Platforms Inc.", "sector": "Communication Services"},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway", "sector": "Financials"},
    {"symbol": "TSLA", "name": "Tesla Inc.", "sector": "Consumer Discretionary"},
    {"symbol": "AVGO", "name": "Broadcom Inc.", "sector": "Information Technology"},
    {"symbol": "JPM", "name": "JPMorgan Chase & Co.", "sector": "Financials"},
    {"symbol": "LLY", "name": "Eli Lilly and Company", "sector": "Healthcare"},
    {"symbol": "V", "name": "Visa Inc.", "sector": "Financials"},
    {"symbol": "XOM", "name": "Exxon Mobil Corporation", "sector": "Energy"},
    {"symbol": "UNH", "name": "UnitedHealth Group", "sector": "Healthcare"},
    {"symbol": "MA", "name": "Mastercard Incorporated", "sector": "Financials"},
    {"symbol": "COST", "name": "Costco Wholesale Corporation", "sector": "Consumer Staples"},
    {"symbol": "HD", "name": "Home Depot Inc.", "sector": "Consumer Discretionary"},
    {"symbol": "PG", "name": "Procter & Gamble Co.", "sector": "Consumer Staples"},
    {"symbol": "NFLX", "name": "Netflix Inc.", "sector": "Communication Services"},
    {"symbol": "JNJ", "name": "Johnson & Johnson", "sector": "Healthcare"},
    {"symbol": "BAC", "name": "Bank of America Corp", "sector": "Financials"},
    {"symbol": "ABBV", "name": "AbbVie Inc.", "sector": "Healthcare"},
    {"symbol": "CRM", "name": "Salesforce Inc.", "sector": "Information Technology"},
    {"symbol": "ORCL", "name": "Oracle Corporation", "sector": "Information Technology"},
    {"symbol": "WMT", "name": "Walmart Inc.", "sector": "Consumer Staples"},
    {"symbol": "KO", "name": "Coca-Cola Company", "sector": "Consumer Staples"},
    {"symbol": "CVX", "name": "Chevron Corporation", "sector": "Energy"},
    {"symbol": "MRK", "name": "Merck & Co.", "sector": "Healthcare"},
    {"symbol": "AMD", "name": "Advanced Micro Devices", "sector": "Information Technology"},
    {"symbol": "PEP", "name": "PepsiCo Inc.", "sector": "Consumer Staples"},
    {"symbol": "ADBE", "name": "Adobe Inc.", "sector": "Information Technology"},
    {"symbol": "CSCO", "name": "Cisco Systems Inc.", "sector": "Information Technology"},
    {"symbol": "TMO", "name": "Thermo Fisher Scientific", "sector": "Healthcare"},
    {"symbol": "ACN", "name": "Accenture plc", "sector": "Information Technology"},
    {"symbol": "MCD", "name": "McDonald's Corporation", "sector": "Consumer Discretionary"},
    {"symbol": "ABT", "name": "Abbott Laboratories", "sector": "Healthcare"},
    {"symbol": "LIN", "name": "Linde plc", "sector": "Materials"},
    {"symbol": "DHR", "name": "Danaher Corporation", "sector": "Healthcare"},
    {"symbol": "WFC", "name": "Wells Fargo & Company", "sector": "Financials"},
    {"symbol": "TXN", "name": "Texas Instruments", "sector": "Information Technology"},
    {"symbol": "PM", "name": "Philip Morris International", "sector": "Consumer Staples"},
    {"symbol": "NEE", "name": "NextEra Energy", "sector": "Utilities"},
    {"symbol": "DIS", "name": "Walt Disney Company", "sector": "Communication Services"},
    {"symbol": "IBM", "name": "IBM", "sector": "Information Technology"},
    {"symbol": "GE", "name": "General Electric Company", "sector": "Industrials"},
    {"symbol": "INTU", "name": "Intuit Inc.", "sector": "Information Technology"},
    {"symbol": "CAT", "name": "Caterpillar Inc.", "sector": "Industrials"},
    {"symbol": "VZ", "name": "Verizon Communications", "sector": "Communication Services"},
    {"symbol": "PFE", "name": "Pfizer Inc.", "sector": "Healthcare"},
    {"symbol": "AMGN", "name": "Amgen Inc.", "sector": "Healthcare"},
    {"symbol": "QCOM", "name": "QUALCOMM Incorporated", "sector": "Information Technology"},
    {"symbol": "SPGI", "name": "S&P Global Inc.", "sector": "Financials"},
    {"symbol": "HON", "name": "Honeywell International", "sector": "Industrials"},
    {"symbol": "UNP", "name": "Union Pacific Corporation", "sector": "Industrials"},
    {"symbol": "LOW", "name": "Lowe's Companies Inc.", "sector": "Consumer Discretionary"},
    {"symbol": "COP", "name": "ConocoPhillips", "sector": "Energy"},
    {"symbol": "BA", "name": "Boeing Company", "sector": "Industrials"},
    {"symbol": "SCHW", "name": "Charles Schwab Corp", "sector": "Financials"},
    {"symbol": "T", "name": "AT&T Inc.", "sector": "Communication Services"},
    {"symbol": "GS", "name": "Goldman Sachs Group", "sector": "Financials"},
    {"symbol": "BLK", "name": "BlackRock Inc.", "sector": "Financials"},
    {"symbol": "MS", "name": "Morgan Stanley", "sector": "Financials"},
    {"symbol": "AXP", "name": "American Express Company", "sector": "Financials"},
    {"symbol": "SBUX", "name": "Starbucks Corporation", "sector": "Consumer Discretionary"},
    {"symbol": "DE", "name": "Deere & Company", "sector": "Industrials"},
    {"symbol": "MDT", "name": "Medtronic plc", "sector": "Healthcare"},
    {"symbol": "GILD", "name": "Gilead Sciences Inc.", "sector": "Healthcare"},
    {"symbol": "ADI", "name": "Analog Devices Inc.", "sector": "Information Technology"},
    {"symbol": "LMT", "name": "Lockheed Martin Corp", "sector": "Industrials"},
    {"symbol": "TJX", "name": "TJX Companies Inc.", "sector": "Consumer Discretionary"},
    {"symbol": "MMC", "name": "Marsh & McLennan Companies", "sector": "Financials"},
    {"symbol": "SYK", "name": "Stryker Corporation", "sector": "Healthcare"},
    {"symbol": "PLD", "name": "Prologis Inc.", "sector": "Real Estate"},
    {"symbol": "ELV", "name": "Elevance Health Inc.", "sector": "Healthcare"},
    {"symbol": "MU", "name": "Micron Technology", "sector": "Information Technology"},
    {"symbol": "REGN", "name": "Regeneron Pharmaceuticals", "sector": "Healthcare"},
    {"symbol": "C", "name": "Citigroup Inc.", "sector": "Financials"},
    {"symbol": "NOW", "name": "ServiceNow Inc.", "sector": "Information Technology"},
    {"symbol": "UPS", "name": "United Parcel Service", "sector": "Industrials"},
    {"symbol": "ISRG", "name": "Intuitive Surgical Inc.", "sector": "Healthcare"},
    {"symbol": "BKNG", "name": "Booking Holdings Inc.", "sector": "Consumer Discretionary"},
]


def get_sp500_symbols():
    """S&P 500 constituent list from Wikipedia (free, no key), with a fallback
    list of known large caps if Wikipedia blocks the request or the table
    layout changes. NOTE: Wikipedia commonly 403s requests that don't look
    like a real browser, so this uses the same spoofed User-Agent as the NSE
    fetch below rather than letting pandas hit the URL directly."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        df = tables[0].rename(
            columns={"Symbol": "symbol", "Security": "name", "GICS Sector": "sector"}
        )
        # yfinance wants BRK-B, not BRK.B
        df["symbol"] = df["symbol"].astype(str).str.replace(".", "-", regex=False)
        records = df[["symbol", "name", "sector"]].to_dict("records")
        if not records:
            raise ValueError("empty S&P 500 table")
        return records
    except Exception as exc:
        print(
            f"WARNING: S&P 500 list fetch from Wikipedia failed ({exc}); "
            f"using the {len(SP500_FALLBACK)}-symbol fallback list instead",
            file=sys.stderr,
        )
        return SP500_FALLBACK


def get_nifty500_symbols():
    """Nifty 500 constituent list from the NSE archives CSV, with a fallback
    list of known large caps if NSE blocks the request (common from cloud IPs)."""
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        df = df.rename(
            columns={"Symbol": "symbol", "Company Name": "name", "Industry": "sector"}
        )
        records = df[["symbol", "name", "sector"]].to_dict("records")
        if not records:
            raise ValueError("empty NSE list")
        return records
    except Exception as exc:
        print(
            f"WARNING: NSE Nifty 500 list fetch failed ({exc}); "
            f"using the {len(NIFTY500_FALLBACK)}-symbol fallback list instead",
            file=sys.stderr,
        )
        return NIFTY500_FALLBACK


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_rsi(close: pd.Series, period: int = 14):
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    last_gain, last_loss = avg_gain.iloc[-1], avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return 100 - (100 / (1 + rs))


def compute_metrics(hist: pd.DataFrame):
    """hist: yfinance history DataFrame (Date index, OHLCV), longest available period."""
    close = hist["Close"].dropna()
    if close.empty:
        return None

    price = float(close.iloc[-1])
    all_time_high = float(close.max())

    last_252 = close.tail(252)
    week52_low = float(last_252.min())
    week52_high = float(last_252.max())

    def sma(n):
        return float(close.tail(n).mean()) if len(close) >= n else None

    sma20, sma50, sma100, sma200 = sma(20), sma(50), sma(100), sma(200)
    rsi = compute_rsi(close)

    volume_trend_pct = None
    vol = hist["Volume"].dropna()
    if len(vol) >= 100:
        recent = vol.tail(10).mean()
        baseline = vol.tail(100).head(90).mean()
        if baseline and baseline > 0:
            volume_trend_pct = round((recent / baseline - 1) * 100, 1)

    trend = "Mixed"
    if sma50 and sma200:
        if price > sma50 > sma200:
            trend = "Uptrend"
        elif price < sma50 < sma200:
            trend = "Downtrend"

    buying_range_basis = []
    if sma50 and abs(price / sma50 - 1) <= BUY_RANGE_PCT:
        buying_range_basis.append("50DMA")
    if sma100 and abs(price / sma100 - 1) <= BUY_RANGE_PCT:
        buying_range_basis.append("100DMA")

    return {
        "price": round(price, 2),
        "week52_low": round(week52_low, 2),
        "week52_high": round(week52_high, 2),
        "all_time_high": round(all_time_high, 2),
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma100": round(sma100, 2) if sma100 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "volume_trend_pct": volume_trend_pct,
        "trend": trend,
        "buying_range": len(buying_range_basis) > 0,
        "buying_range_basis": buying_range_basis,
    }


def _num(info: dict, key: str):
    v = info.get(key)
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return v
    return None


def extract_fundamentals(info: dict):
    revenue_growth = _num(info, "revenueGrowth")
    earnings_growth = _num(info, "earningsGrowth")
    return {
        "market_cap": _num(info, "marketCap"),
        "pe_ratio": _num(info, "trailingPE"),
        "debt_to_equity": _num(info, "debtToEquity"),
        "revenue_growth_yoy_pct": round(revenue_growth * 100, 1) if revenue_growth is not None else None,
        "earnings_growth_yoy_pct": round(earnings_growth * 100, 1) if earnings_growth is not None else None,
    }


def safe_get_info(ticker: yf.Ticker, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            info = ticker.get_info() if hasattr(ticker, "get_info") else ticker.info
            if info:
                return info
        except Exception:
            if attempt == retries:
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


# ---------------------------------------------------------------------------
# Per-market pipeline
# ---------------------------------------------------------------------------
def process_market(market_name, candidates, min_cap, yf_suffix=""):
    records, errors = [], []
    for i, c in enumerate(candidates):
        yf_symbol = f"{c['symbol']}{yf_suffix}"
        try:
            tk = yf.Ticker(yf_symbol)
            info = safe_get_info(tk)
            market_cap = _num(info, "marketCap")
            if not market_cap or market_cap < min_cap:
                continue  # below the screening floor — skip the (expensive) history pull

            hist = tk.history(period="max", auto_adjust=False)
            metrics = compute_metrics(hist)
            if metrics is None:
                continue

            record = {
                "symbol": c["symbol"],
                "yf_symbol": yf_symbol,
                "name": c.get("name") or info.get("shortName") or c["symbol"],
                "sector": c.get("sector") or info.get("sector") or "Unknown",
                **extract_fundamentals(info),
                **metrics,
            }
            records.append(record)
            print(f"[{market_name}] {i + 1}/{len(candidates)} {yf_symbol}: in scope (cap {market_cap:,.0f})")
        except Exception as exc:
            errors.append({"symbol": yf_symbol, "error": str(exc)})
            print(f"[{market_name}] {i + 1}/{len(candidates)} {yf_symbol}: ERROR {exc}", file=sys.stderr)
        time.sleep(REQUEST_SLEEP_SEC)

    # Sector-average P/E, computed within this market's in-scope universe
    sector_pes = {}
    for r in records:
        if r.get("pe_ratio"):
            sector_pes.setdefault(r["sector"], []).append(r["pe_ratio"])
    sector_avg = {s: round(sum(v) / len(v), 1) for s, v in sector_pes.items()}
    for r in records:
        r["sector_pe_avg"] = sector_avg.get(r["sector"])

    records.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
    return records, errors


def _sanitize_for_json(obj):
    """Recursively replace NaN/Infinity with None. Python's json module will
    happily write literal NaN/Infinity tokens (technically invalid JSON), and
    the dashboard's `fetch(...).then(r => r.json())` call would throw on that
    and fail to load the whole page — better to degrade to a blank cell."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def main():
    print("Fetching S&P 500 candidate list...")
    us_candidates = get_sp500_symbols()
    print(f"  {len(us_candidates)} candidates")

    print("Fetching Nifty 500 candidate list...")
    india_candidates = get_nifty500_symbols()
    print(f"  {len(india_candidates)} candidates")

    print("Processing US market (this can take a while — one request per candidate)...")
    us_records, us_errors = process_market("US", us_candidates, US_MIN_MARKET_CAP, yf_suffix="")

    print("Processing India market...")
    india_records, india_errors = process_market(
        "India", india_candidates, INDIA_MIN_MARKET_CAP_INR, yf_suffix=".NS"
    )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "us_min_market_cap": US_MIN_MARKET_CAP,
            "india_min_market_cap_inr": INDIA_MIN_MARKET_CAP_INR,
            "buying_range_pct": BUY_RANGE_PCT,
        },
        "markets": {
            "US": us_records,
            "India": india_records,
        },
        "meta": {
            "us_candidates_scanned": len(us_candidates),
            "us_in_scope": len(us_records),
            "us_errors": len(us_errors),
            "india_candidates_scanned": len(india_candidates),
            "india_in_scope": len(india_records),
            "india_errors": len(india_errors),
        },
    }

    output = _sanitize_for_json(output)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(
        f"\nWrote {OUTPUT_PATH}: US {len(us_records)} in scope "
        f"({len(us_errors)} errors), India {len(india_records)} in scope "
        f"({len(india_errors)} errors)"
    )


if __name__ == "__main__":
    main()
