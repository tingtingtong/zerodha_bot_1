import logging
import os
from datetime import datetime, timedelta
from typing import List
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

NEGATIVE_WORDS = ["fraud", "scam", "loss", "default", "downgrade", "crash", "penalty",
                  "sebi", "ban", "suspend", "probe", "investigation", "resign", "cut",
                  "miss", "below estimate", "fire", "accident"]
POSITIVE_WORDS = ["profit", "growth", "upgrade", "beat", "record", "acquire",
                  "expansion", "win", "gain", "above estimate", "positive"]


class NewsItem:
    def __init__(self, title: str, source: str, published_at: datetime,
                 symbol: str = "", sentiment: str = "neutral", url: str = ""):
        self.title = title
        self.source = source
        self.published_at = published_at
        self.symbol = symbol
        self.sentiment = sentiment
        self.url = url

    def is_recent(self, hours: int = 24) -> bool:
        now = datetime.now(IST)
        pub = self.published_at
        if pub.tzinfo is None:
            pub = IST.localize(pub)
        return (now - pub).total_seconds() < hours * 3600


class NewsProvider:

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("NEWSAPI_KEY", "")
        self._enabled = bool(self.api_key)

    def get_news(self, symbol: str, hours: int = 24) -> List[NewsItem]:
        if not self._enabled:
            return []
        try:
            import requests
            from_dt = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={"q": symbol, "from": from_dt, "language": "en",
                        "sortBy": "publishedAt", "apiKey": self.api_key, "pageSize": 10},
                timeout=5,
            )
            resp.raise_for_status()
            items = []
            for a in resp.json().get("articles", []):
                pub = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00"))
                items.append(NewsItem(
                    title=a.get("title", ""), source=a.get("source", {}).get("name", ""),
                    published_at=pub, symbol=symbol,
                    sentiment=self._sentiment(a.get("title", "")),
                    url=a.get("url", ""),
                ))
            return items
        except Exception as e:
            logger.warning(f"News fetch failed {symbol}: {e}")
            return []

    def has_negative_news(self, symbol: str, hours: int = 6) -> bool:
        return any(i.sentiment == "negative" for i in self.get_news(symbol, hours))

    def _sentiment(self, text: str) -> str:
        t = text.lower()
        neg = sum(1 for w in NEGATIVE_WORDS if w in t)
        pos = sum(1 for w in POSITIVE_WORDS if w in t)
        if neg > pos:
            return "negative"
        if pos > neg:
            return "positive"
        return "neutral"
