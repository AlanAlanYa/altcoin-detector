"""
api_client.py - 完美補齊版：補回 get_order_book 並強化限頻保護
"""
import time
import logging
import requests
from typing import Optional, Dict, Any, List
from src.config import config

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, min_interval: float = 0.5):
        self.min_interval = min_interval
        self._last_call_time: float = 0.0
    def wait(self):
        elapsed = time.time() - self._last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_time = time.time()

_rate_limiter = RateLimiter()

def _safe_request(method: str, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            _rate_limiter.wait()
            response = requests.request(method, url, params=params, headers=headers, timeout=20)
            
            if response.status_code == 429:
                wait_time = 30 * attempt # 加重冷卻時間
                logger.warning(f"⚠️ 觸發限速 (429)，冷卻 {wait_time} 秒...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == retries:
                logger.error(f"❌ 請求最終失敗: {url} | 錯誤: {e}")
            time.sleep(2)
    return None

class BinanceClient:
    SPOT_BASE = "https://api.mexc.com"  

    # --- 現貨方法 ---
    def get_klines(self, symbol: str, interval: str, limit: int = 200):
        return _safe_request("GET", f"{self.SPOT_BASE}/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})

    def get_exchange_info(self):
        return _safe_request("GET", f"{self.SPOT_BASE}/api/v3/exchangeInfo")

    def get_ticker_24h(self, symbol: str):
        return _safe_request("GET", f"{self.SPOT_BASE}/api/v3/ticker/24hr", params={"symbol": symbol})

    def get_recent_trades(self, symbol: str, limit: int = 1000):
        return _safe_request("GET", f"{self.SPOT_BASE}/api/v3/trades", params={"symbol": symbol, "limit": limit})

    def get_order_book(self, symbol: str, limit: int = 20):
        # 💡 補回缺失的方法：取得訂單簿深度，計算 2% 流動性
        return _safe_request("GET", f"{self.SPOT_BASE}/api/v3/depth", params={"symbol": symbol, "limit": limit})

    # --- 合約方法 ---
    def get_open_interest(self, symbol: str):
        return _safe_request("GET", f"{self.FUTURES_BASE}/fapi/v1/openInterest", params={"symbol": symbol})

    def get_open_interest_hist(self, symbol: str, period: str = "15m", limit: int = 20):
        return _safe_request("GET", f"{self.FUTURES_BASE}/futures/data/openInterestHist", params={"symbol": symbol, "period": period, "limit": limit})

    def get_funding_rate(self, symbol: str):
        return _safe_request("GET", f"{self.FUTURES_BASE}/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 1})

    def get_top_trader_ls_ratio(self, symbol: str, period: str = "15m", limit: int = 5):
        return _safe_request("GET", f"{self.FUTURES_BASE}/futures/data/topLongShortPositionRatio", params={"symbol": symbol, "period": period, "limit": limit})

class CoinGeckoClient:
    BASE = config.COINGECKO_BASE_URL
    def _headers(self):
        key = str(config.COINGECKO_API_KEY).strip()
        return {"accept": "application/json", "x-cg-demo-api-key": key, "User-Agent": "Mozilla/5.0"}
    
    def get_coins_markets(self, vs_currency="usd", per_page=250, page=1):
        url = f"{self.BASE}/coins/markets"
        params = {"vs_currency": vs_currency, "order": "market_cap_desc", "per_page": per_page, "page": page, "sparkline": "false", "price_change_percentage": "24h"}
        return _safe_request("GET", url, params=params, headers=self._headers())

    def get_coin_market_chart(self, coin_id: str, vs_currency: str = "usd", days: int = 14):
        url = f"{self.BASE}/coins/{coin_id}/market_chart"
        params = {"vs_currency": vs_currency, "days": days, "interval": "daily"}
        return _safe_request("GET", url, params=params, headers=self._headers())

binance = BinanceClient()
coingecko = CoinGeckoClient()