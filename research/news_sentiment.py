"""
News Sentiment — fetches RSS feeds from Economic Times markets section
and scores each watchlist symbol as positive / neutral / negative.

No API key required. Falls back gracefully on any network failure.
"""
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ── RSS feed URLs ──────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ── Sentiment keywords ─────────────────────────────────────────────────────────
NEGATIVE_KEYWORDS = [
    "fraud", "scam", "default", "downgrade", "loss", "penalty", "raid",
    "sebi notice", "sebi order", "promoter sold", "block deal sell",
    "debt concern", "warning", "investigation", "probe", "fir",
    "insolvency", "bankruptcy", "npa", "write-off", "cut rating",
    "miss estimates", "below estimate", "quarterly loss", "net loss",
    "revenue decline", "slowdown", "layoff", "plant shutdown",
]

POSITIVE_KEYWORDS = [
    "buyback", "order win", "beat estimates", "upgrade", "expansion",
    "acquisition", "dividend", "profit rise", "block deal buy",
    "promoter buy", "strong results", "record revenue", "new high",
    "contract win", "export order", "capex plan", "margin expansion",
    "above estimate", "quarterly profit", "net profit up",
]


@dataclass
class SentimentResult:
    symbol: str
    headline_count: int
    sentiment_score: float          # -1.0 (very negative) to +1.0 (very positive)
    is_flagged: bool                # True = negative news, avoid trading
    is_boosted: bool                # True = positive news, favour trading
    headlines: List[str] = field(default_factory=list)


def _fetch_rss_headlines(cache_dir: str, cache_ttl_minutes: int) -> List[str]:
    """Fetch and cache RSS headlines. Returns list of headline strings."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    # Round to nearest TTL bucket for cache key
    now = datetime.now()
    bucket = (now.minute // cache_ttl_minutes) * cache_ttl_minutes
    cache_file = cache_path / f"news_rss_{now.strftime('%Y-%m-%d')}_{now.hour:02d}{bucket:02d}.json"

    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except Exception:
            pass

    headlines = []
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title_el = item.find("title")
                desc_el = item.find("description")
                if title_el is not None and title_el.text:
                    headlines.append(title_el.text.strip().lower())
                if desc_el is not None and desc_el.text:
                    headlines.append(desc_el.text.strip().lower())
        except Exception as e:
            logger.debug(f"RSS fetch failed for {feed_url}: {e}")

    if headlines:
        try:
            with open(cache_file, "w") as f:
                json.dump(headlines, f)
        except Exception:
            pass

    return headlines


def _score_symbol(symbol: str, headlines: List[str],
                  negative_threshold: float,
                  positive_threshold: float) -> SentimentResult:
    """Score a single symbol against all headlines."""
    sym_lower = symbol.lower()
    # Match ticker or common name variants
    pattern = re.compile(r'\b' + re.escape(sym_lower) + r'\b')

    matching = [h for h in headlines if pattern.search(h)]
    if not matching:
        return SentimentResult(
            symbol=symbol, headline_count=0,
            sentiment_score=0.0, is_flagged=False, is_boosted=False,
        )

    pos = sum(1 for h in matching if any(k in h for k in POSITIVE_KEYWORDS))
    neg = sum(1 for h in matching if any(k in h for k in NEGATIVE_KEYWORDS))
    total = max(len(matching), 1)
    score = (pos - neg) / total
    score = max(-1.0, min(1.0, score))

    return SentimentResult(
        symbol=symbol,
        headline_count=len(matching),
        sentiment_score=round(score, 3),
        is_flagged=score < negative_threshold,
        is_boosted=score > positive_threshold,
        headlines=matching[:5],  # keep top 5 for logging
    )


def fetch_news_sentiment(
    symbols: List[str],
    cache_dir: str = ".cache/market_data",
    cache_ttl_minutes: int = 30,
    negative_threshold: float = -0.3,
    positive_threshold: float = 0.4,
) -> Dict[str, SentimentResult]:
    """
    Fetch news sentiment for a list of symbols.

    Returns dict of {symbol: SentimentResult}.
    On any failure returns {} — caller treats missing as neutral.
    """
    try:
        headlines = _fetch_rss_headlines(cache_dir, cache_ttl_minutes)
        if not headlines:
            logger.debug("News sentiment: no headlines fetched — treating all as neutral")
            return {}

        results = {}
        for sym in symbols:
            results[sym] = _score_symbol(sym, headlines, negative_threshold, positive_threshold)

        flagged = [s for s, r in results.items() if r.is_flagged]
        boosted = [s for s, r in results.items() if r.is_boosted]
        mentioned = [s for s, r in results.items() if r.headline_count > 0]

        if mentioned:
            logger.info(
                f"News sentiment: {len(mentioned)} symbols mentioned | "
                f"Flagged negative: {flagged} | Boosted positive: {boosted}"
            )
        else:
            logger.debug("News sentiment: no watchlist symbols mentioned in today's headlines")

        return results

    except Exception as e:
        logger.warning(f"News sentiment fetch failed (non-critical): {e}")
        return {}
