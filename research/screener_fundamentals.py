"""
Screener.in Fundamentals — fetches key fundamental ratios for watchlist symbols.

Scrapes the public Screener.in company pages (no login required for basic data).
Filters by: debt-to-equity < threshold, promoter holding %, positive earnings trend.

Cached 24h per symbol — fundamentals don't change intraday.
Falls back gracefully on any network/parse failure.
"""
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SCREENER_BASE = "https://www.screener.in/company/{symbol}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/",
}


@dataclass
class FundamentalsResult:
    symbol: str
    debt_to_equity: Optional[float]       # None = couldn't fetch
    promoter_holding_pct: Optional[float]
    quarterly_profit_trend: str            # "improving" | "declining" | "flat" | "unknown"
    passes_filter: bool
    filter_reason: str                     # why passed/failed, for logging
    score_adjustment: int = 0             # positive = boost, negative = penalty


def _parse_number(text: str) -> Optional[float]:
    """Extract first float from a string like '₹1,234.56 Cr' → 1234.56."""
    if not text:
        return None
    cleaned = re.sub(r'[^\d.\-]', '', text.replace(',', ''))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _fetch_company_page(symbol: str, session: requests.Session) -> dict:
    """
    Fetch key ratios from Screener.in company page.
    Returns dict with debt_to_equity, promoter_holding, quarterly_profits list.
    """
    url = SCREENER_BASE.format(symbol=symbol)
    resp = session.get(url, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    data = {"debt_to_equity": None, "promoter_holding": None, "quarterly_profits": []}

    # ── Key ratios (top summary section) ──────────────────────────
    for li in soup.select("#top-ratios li"):
        label_el = li.select_one(".name")
        value_el = li.select_one(".value, .number")
        if not label_el or not value_el:
            continue
        label = label_el.get_text(strip=True).lower()
        value_text = value_el.get_text(strip=True)

        if "debt" in label and "equity" in label:
            data["debt_to_equity"] = _parse_number(value_text)
        elif "debt / equity" in label or "d/e" in label:
            data["debt_to_equity"] = _parse_number(value_text)

    # ── Promoter holding from shareholding section ─────────────────
    for row in soup.select("table.data-table tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if "promoter" in label:
                # Last cell is most recent quarter
                val = _parse_number(cells[-1].get_text(strip=True))
                if val is not None:
                    data["promoter_holding"] = val
                    break

    # ── Quarterly profits from results table ───────────────────────
    for section in soup.select("section"):
        h2 = section.find("h2")
        if h2 and "quarterly" in h2.get_text(strip=True).lower():
            for row in section.select("table.data-table tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    if "net profit" in label or "profit after tax" in label:
                        profits = []
                        for cell in cells[1:5]:  # last 4 quarters
                            v = _parse_number(cell.get_text(strip=True))
                            if v is not None:
                                profits.append(v)
                        data["quarterly_profits"] = profits
                        break
            break

    return data


def _evaluate(
    raw: dict,
    max_debt_equity: float,
    min_promoter_pct: float,
    require_positive_earnings: bool,
) -> FundamentalsResult:
    """Apply filter thresholds and return a FundamentalsResult."""
    de = raw.get("debt_to_equity")
    promoter = raw.get("promoter_holding")
    profits = raw.get("quarterly_profits", [])

    reasons = []
    penalties = 0
    boosts = 0

    # Debt-to-equity check
    if de is not None:
        if de > max_debt_equity:
            reasons.append(f"high D/E={de:.1f}")
            penalties += 1
        elif de < 0.1:
            boosts += 1  # nearly debt-free

    # Promoter holding check
    if promoter is not None:
        if promoter < min_promoter_pct:
            reasons.append(f"low promoter={promoter:.0f}%")
            penalties += 1
        elif promoter > 65:
            boosts += 1  # strong promoter confidence

    # Earnings trend
    trend = "unknown"
    if len(profits) >= 3:
        if all(p > 0 for p in profits):
            trend = "improving" if profits[-1] > profits[0] else "flat"
            if profits[-1] > profits[0]:
                boosts += 1
        else:
            # Any negative quarter
            negative_quarters = sum(1 for p in profits if p <= 0)
            if negative_quarters > 0:
                trend = "declining"
                if require_positive_earnings:
                    reasons.append(f"{negative_quarters} loss quarter(s)")
                    penalties += 1

    passes = penalties == 0
    score_adj = (boosts * 5) - (penalties * 10)

    return FundamentalsResult(
        symbol=raw.get("symbol", ""),
        debt_to_equity=de,
        promoter_holding_pct=promoter,
        quarterly_profit_trend=trend,
        passes_filter=passes,
        filter_reason="ok" if passes else "; ".join(reasons),
        score_adjustment=score_adj,
    )


def fetch_fundamentals(
    symbols: List[str],
    cache_dir: str = ".cache/market_data",
    cache_ttl_hours: int = 24,
    max_debt_equity: float = 0.5,
    min_promoter_holding_pct: float = 50.0,
    require_positive_earnings: bool = True,
    request_delay_sec: float = 0.8,
) -> Dict[str, FundamentalsResult]:
    """
    Fetch fundamental data for a list of symbols from Screener.in.

    Returns dict of {symbol: FundamentalsResult}.
    Symbols that fail to fetch default to passes_filter=True (optimistic).
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()

    session = requests.Session()
    session.headers.update(HEADERS)

    results = {}
    fetched = 0
    cached = 0

    for symbol in symbols:
        cache_file = cache_path / f"screener_{symbol}_{today_str}.json"

        # Try cache first
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    raw = json.load(f)
                raw["symbol"] = symbol
                result = _evaluate(raw, max_debt_equity, min_promoter_holding_pct, require_positive_earnings)
                results[symbol] = result
                cached += 1
                continue
            except Exception:
                pass

        # Live fetch
        try:
            raw = _fetch_company_page(symbol, session)
            raw["symbol"] = symbol
            # Cache raw data
            try:
                with open(cache_file, "w") as f:
                    json.dump(raw, f)
            except Exception:
                pass
            result = _evaluate(raw, max_debt_equity, min_promoter_holding_pct, require_positive_earnings)
            results[symbol] = result
            fetched += 1
            time.sleep(request_delay_sec)  # polite rate limit

        except Exception as e:
            logger.debug(f"Screener fetch failed for {symbol}: {e}")
            # Optimistic default — don't block on missing data
            results[symbol] = FundamentalsResult(
                symbol=symbol,
                debt_to_equity=None,
                promoter_holding_pct=None,
                quarterly_profit_trend="unknown",
                passes_filter=True,
                filter_reason="fetch_failed",
                score_adjustment=0,
            )

    failed = [s for s, r in results.items() if not r.passes_filter]
    logger.info(
        f"Screener fundamentals: {fetched} fetched, {cached} cached, "
        f"{len(results)} total | Failed filter: {failed}"
    )
    return results
