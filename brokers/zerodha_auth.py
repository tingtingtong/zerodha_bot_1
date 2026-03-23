import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
TOKEN_FILE = "config/.zerodha_token.json"


class ZerodhaTokenManager:

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key or os.getenv("ZERODHA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ZERODHA_API_SECRET", "")
        if not self.api_key:
            raise ValueError("ZERODHA_API_KEY not set")
        from kiteconnect import KiteConnect
        self.kite = KiteConnect(api_key=self.api_key)
        self._access_token: Optional[str] = None

    def get_login_url(self) -> str:
        return self.kite.login_url()

    def generate_token(self, request_token: str) -> str:
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        token = data["access_token"]
        self._save_token(token)
        self._access_token = token
        logger.info("Zerodha access token generated.")
        return token

    def load_token(self) -> str:
        path = Path(TOKEN_FILE)
        if not path.exists():
            raise FileNotFoundError(f"No token file. Login at: {self.get_login_url()}")
        with open(path) as f:
            data = json.load(f)
        if data.get("date") != str(date.today()):
            raise ValueError(f"Stale token from {data.get('date')}. Re-auth: {self.get_login_url()}")
        self._access_token = data["access_token"]
        self.kite.set_access_token(self._access_token)
        logger.info("Zerodha token loaded.")
        return self._access_token

    def is_session_valid(self) -> bool:
        try:
            self.kite.profile()
            return True
        except Exception:
            return False

    def get_kite(self):
        if not self._access_token:
            self.load_token()
        return self.kite

    def _save_token(self, token: str):
        path = Path(TOKEN_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"access_token": token, "date": str(date.today()),
                       "timestamp": datetime.now().isoformat()}, f, indent=2)
