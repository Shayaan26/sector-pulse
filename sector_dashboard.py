#!/usr/bin/env python3
"""
Sector Pulse: S&P 500 sector heatmap with top performers per sector.

Pulls free prices from Yahoo Finance (via the yfinance package), works out each
stock's move versus the previous close, and writes dashboard.html next to this
script, then opens it in your browser.

Setup (once):   pip install yfinance pandas tzdata
Run:            python sector_dashboard.py
Keep updating:  python sector_dashboard.py --every 5     (rebuilds every 5 min;
                                                           the page reloads itself)
Market session: python sector_dashboard.py --market-hours
                Waits for the US open (9:30 New York time), rebuilds every 5 min
                until the close, does one last build after the bell, then exits.
                Weekends and NYSE holidays are skipped. schedule_windows.bat
                sets this up to start by itself every weekday.
"""
import argparse
import csv
import io
import json
import sys
import time
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "dashboard_template.html"
OUTPUT = HERE / "dashboard.html"
CONSTITUENTS_LOCAL = HERE / "sp500_constituents.csv"
CAP_CACHE = HERE / "market_caps.json"
CONSTITUENTS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
CAP_MAX_AGE_DAYS = 7

# NYSE full-day closures and 1 p.m. early closes. Update this list each year.
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}


def load_constituents():
    """Current S&P 500 list with GICS sectors. Tries online first, then the local copy."""
    text = None
    try:
        with urllib.request.urlopen(CONSTITUENTS_URL, timeout=15) as r:
            text = r.read().decode("utf-8")
        CONSTITUENTS_LOCAL.write_text(text, encoding="utf-8")
    except Exception as e:
        if CONSTITUENTS_LOCAL.exists():
            print(f"Couldn't refresh the S&P 500 list ({e}); using the saved copy.")
            text = CONSTITUENTS_LOCAL.read_text(encoding="utf-8")
        else:
            sys.exit(f"Couldn't download the S&P 500 list and no saved copy exists: {e}")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [
        {"t": r["Symbol"].strip(), "n": r["Security"].strip(), "s": r["GICS Sector"].strip()}
        for r in rows if r.get("Symbol")
    ]


def yahoo_symbol(t):
    return t.replace(".", "-")  # BRK.B -> BRK-B


def fetch_market_caps(tickers, yf):
    """Market caps change slowly, so they're cached for a week (first run takes ~1 minute)."""
    cache = {}
    if CAP_CACHE.exists():
        try:
            cache = json.loads(CAP_CACHE.read_text())
        except Exception:
            cache = {}
    fresh = cache.get("_saved", 0) > time.time() - CAP_MAX_AGE_DAYS * 86400
    caps = cache.get("caps", {}) if fresh else {}
    missing = [t for t in tickers if t not in caps]
    if missing:
        print(f"Fetching market caps for {len(missing)} stocks (cached for {CAP_MAX_AGE_DAYS} days)...")

        def one(t):
            try:
                return t, float(yf.Ticker(yahoo_symbol(t)).fast_info["marketCap"])
            except Exception:
                return t, None

        with ThreadPoolExecutor(max_workers=16) as ex:
            for t, c in ex.map(one, missing):
                if c:
                    caps[t] = c
        CAP_CACHE.write_text(json.dumps({"_saved": time.time(), "caps": caps}))
    return caps


def build(open_browser=True, refresh_minutes=None, output=None):
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance isn't installed. Run:  pip install yfinance pandas")

    members = load_constituents()
    tickers = [m["t"] for m in members]
    ysyms = [yahoo_symbol(t) for t in tickers]

    print(f"Downloading prices for {len(tickers)} stocks from Yahoo Finance...")
    df = yf.download(ysyms, period="5d", interval="1d", auto_adjust=False,
                     progress=False, threads=True, group_by="column")
    close = df["Close"].dropna(how="all")
    if len(close) < 2:
        sys.exit("Yahoo returned too little price history. Try again in a minute.")
    last_date, prev_date = close.index[-1], close.index[-2]

    caps = fetch_market_caps(tickers, yf)

    stocks = []
    for m in members:
        col = yahoo_symbol(m["t"])
        if col not in close:
            continue
        series = close[col].dropna()
        if len(series) < 2:
            continue
        last, prev = float(series.iloc[-1]), float(series.iloc[-2])
        if prev <= 0:
            continue
        stocks.append({**m, "pct": round((last / prev - 1) * 100, 3),
                       "price": round(last, 2), "cap": caps.get(m["t"])})

    data = {
        "asOf": str(last_date.date()),
        "prevDate": str(prev_date.date()),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Prices: Yahoo Finance (may be delayed ~15 min). Sectors: GICS via datasets/s-and-p-500-companies",
        "sample": False,
        "stocks": stocks,
    }
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    if refresh_minutes:
        html = html.replace("<!--__REFRESH__-->", f'<meta http-equiv="refresh" content="{int(refresh_minutes * 60)}">')
    out = Path(output) if output else OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} with {len(stocks)} stocks (moves for {data['asOf']} vs {data['prevDate']}).")
    if open_browser:
        webbrowser.open(out.as_uri())


def new_york_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        sys.exit("Time zone data is missing. Run:  pip install tzdata")


def session_for(day):
    """(open, close) in New York time for a trading day, or None if the market is shut."""
    if day.weekday() >= 5 or day.isoformat() in NYSE_HOLIDAYS:
        return None
    tz = new_york_tz()
    open_t = datetime(day.year, day.month, day.day, 9, 30, tzinfo=tz)
    close_h = 13 if day.isoformat() in NYSE_EARLY_CLOSE else 16
    return open_t, datetime(day.year, day.month, day.day, close_h, 0, tzinfo=tz)


def safe_build(**kw):
    try:
        build(**kw)
    except SystemExit as e:
        print(e)
    except Exception as e:
        print(f"Update failed, will retry next round: {e}")


def run_market_session(every, open_browser):
    tz = new_york_tz()
    now = datetime.now(tz)
    session = session_for(now.date())
    if not session:
        print(f"US market is closed today ({now:%A %Y-%m-%d} in New York). Nothing to do.")
        return
    open_t, close_t = session
    final_t = close_t + timedelta(minutes=5)  # one last build with closing prices
    if now >= final_t:
        print("Today's session has ended. Building once with closing prices.")
        safe_build(open_browser=open_browser)
        return
    if now < open_t:
        local_open = open_t.astimezone()
        print(f"Waiting for the open at 9:30 New York time ({local_open:%H:%M} your time)...")
        while datetime.now(tz) < open_t:
            time.sleep(min(30, max(1, (open_t - datetime.now(tz)).total_seconds())))
    first = True
    while datetime.now(tz) < close_t:
        safe_build(open_browser=open_browser and first, refresh_minutes=every)
        first = False
        next_t = datetime.now(tz) + timedelta(minutes=every)
        while datetime.now(tz) < min(next_t, close_t):
            time.sleep(5)
    while datetime.now(tz) < final_t:
        time.sleep(5)
    safe_build(open_browser=open_browser and first)  # no auto-refresh after the close
    print("Market closed. Final dashboard written; see you next trading day.")


def run_hosted(output, every):
    """One build per call, only during the session (plus 15 minutes after the close)."""
    tz = new_york_tz()
    now = datetime.now(tz)
    session = session_for(now.date())
    built = False
    if session and session[0] <= now <= session[1] + timedelta(minutes=15):
        build(open_browser=False, refresh_minutes=every if now < session[1] else None, output=output)
        built = True
    else:
        print(f"Market closed ({now:%a %H:%M} New York). Keeping the last page.")
    gh_out = __import__("os").environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"built={'true' if built else 'false'}\n")


def main():
    ap = argparse.ArgumentParser(description="Build the S&P 500 Sector Pulse dashboard.")
    ap.add_argument("--every", type=float, metavar="MIN", help="rebuild every MIN minutes")
    ap.add_argument("--market-hours", action="store_true",
                    help="run from the US open to the close, rebuilding every 5 min (or --every)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--output", metavar="PATH", help="where to write the page (default: dashboard.html)")
    ap.add_argument("--hosted", action="store_true",
                    help="for a scheduled server job: build once only while the market is open "
                         "(or just after the close), with a 5-minute self-refresh")
    a = ap.parse_args()
    if a.hosted:
        run_hosted(a.output, a.every or 5)
        return
    if a.market_hours:
        run_market_session(a.every or 5, open_browser=not a.no_open)
        return
    build(open_browser=not a.no_open, refresh_minutes=a.every, output=a.output)
    while a.every:
        time.sleep(a.every * 60)
        try:
            build(open_browser=False, refresh_minutes=a.every, output=a.output)
        except SystemExit as e:
            print(e)
        except Exception as e:
            print(f"Update failed, will retry: {e}")


if __name__ == "__main__":
    main()
