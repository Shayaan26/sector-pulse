# Sector Pulse

S&P 500 sector heatmap with each sector's top performers, rebuilt every 5 minutes during US market hours and published with GitHub Pages.

- Prices: Yahoo Finance via `yfinance` (may be ~15 min delayed). Sectors: GICS, from datasets/s-and-p-500-companies.
- The schedule lives in `.github/workflows/update-dashboard.yml`. Run it by hand from the Actions tab with "force" ticked to build outside market hours.
- To run it on your own computer instead: `pip install yfinance pandas tzdata`, then `python sector_dashboard.py`.
