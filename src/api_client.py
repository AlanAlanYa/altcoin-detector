"""
api_client.py - 所有對外 API 請求的統一處理層
包含 Rate Limiting、重試機制、錯誤處理與數值型別強健化
交易所：現貨 MEXC (Binance 相容格式) / 籌碼 CoinGlass
"""
import os
import time
import logging
import requests
from typing import Optional, Dict, Any, List
from src.config import config

logger = logging.getLogger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


class RateLimiter:
    def __init__(self, min_interval: float = 0.1):
        self.min_interval = min_interval
        self._last_call_time: float = 0.0

    def wait(self):
        elapsed = time.time() - self._last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_time = time.time()


_mexc_limiter = RateLimiter(min_interval=0.3)   # MEXC：寬鬆限制
_session = requests.Session()
_cg_limiter = RateLimiter(min_interval=2.1)      # CoinGlass：保守限制
_coingecko_limiter = RateLimiter(min_interval=2.1)  # CoinGecko：30次/分鐘


def _safe_request(
    method: str,
    url: str,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    retries: int = config.MAX_RETRIES,
    rate_limiter: Optional[Any] = None,
) -> Optional[Any]:
    limiter = rate_limiter or _mexc_limiter
    for attempt in range(1, retries + 1):
        is_last = attempt == retries
        try:
            limiter.wait()
            response = _session.request(
                method, url, params=params, headers=headers, timeout=15
            )
            if not response.ok:
                body_preview = response.text[:300].replace("\n", " ")
                logger.warning(
                    f"[嘗試 {attempt}/{retries}] HTTP {response.status_code} | {url} | body: {body_preview}"
                )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            if status == 429:
                wait = config.RETRY_DELAY_SEC * 10
                logger.warning(f"[嘗試 {attempt}/{retries}] 429 Rate Limited，等待 {wait}s")
                time.sleep(wait)
            elif not is_last:
                time.sleep(config.RETRY_DELAY_SEC * attempt)
        except requests.exceptions.Timeout:
            logger.warning(f"[嘗試 {attempt}/{retries}] 請求逾時: {url}")
            if not is_last:
                time.sleep(config.RETRY_DELAY_SEC)
        except requests.exceptions.RequestException as e:
            logger.error(f"[嘗試 {attempt}/{retries}] 請求失敗: {url} | {e}")
            if not is_last:
                time.sleep(config.RETRY_DELAY_SEC)
    logger.error(f"已達最大重試次數，放棄請求: {url}")
    return None


# =============================================
# BinanceClient（現貨打 MEXC，籌碼打 CoinGlass）
# =============================================
class BinanceClient:
    MEXC_SPOT_BASE = config.MEXC_SPOT_URL
    CG_BASE = config.COINGLASS_BASE_URL

    def __init__(self):
        self.cg_key = os.getenv("COINGLASS_API_KEY") or config.COINGLASS_API_KEY

    # --- 現貨端點（MEXC）---

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> Optional[List]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/klines"
        return _safe_request("GET", url, params={"symbol": symbol, "interval": interval, "limit": limit})

    def get_ticker_24h(self, symbol: str) -> Optional[Dict]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/ticker/24hr"
        return _safe_request("GET", url, params={"symbol": symbol})

    def get_exchange_info(self) -> Optional[Dict]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/exchangeInfo"
        return _safe_request("GET", url)

    def get_recent_trades(self, symbol: str, limit: int = 500) -> Optional[List]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/trades"
        return _safe_request("GET", url, params={"symbol": symbol, "limit": limit})

    def get_orderbook(self, symbol: str, limit: int = 20) -> Optional[Dict]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/depth"
        return _safe_request("GET", url, params={"symbol": symbol, "limit": limit})

    # --- 籌碼端點（CoinGlass）---

    def _cg_request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        if not self.cg_key:
            logger.error("缺少 COINGLASS_API_KEY")
            return None
        headers = {"accept": "application/json", "cg-api-key": self.cg_key}
        return _safe_request("GET", f"{self.CG_BASE}/{endpoint}", params=params, headers=headers, retries=1, rate_limiter=_cg_limiter)

    def get_open_interest(self, symbol: str) -> Optional[List]:
        data = self._cg_request(
            "open-interest/history",
            {"exchange": "Binance", "symbol": symbol, "interval": "h1", "limit": 5}
        )
        if data and data.get("success"):
            items = data.get("data", [])
            if isinstance(items, list):
                return [{"openInterest": _safe_float(item.get("close"))} for item in items]
        return None

    def get_cvd(self, symbol: str) -> Optional[List]:
        # CoinGlass V4 無 CVD 端點，由 detector 走 MEXC fallback
        return None

    def get_funding_rate(self, symbol: str) -> Optional[List]:
        clean_symbol = symbol.replace("USDT", "")
        data = self._cg_request("funding-rate/exchange-list", {"symbol": clean_symbol})
        if data and data.get("success"):
            items = data.get("data", {}).get("stablecoin_margin_list", [])
            if isinstance(items, list) and items:
                target = next((i for i in items if i.get("exchange") == "Binance"), items[0])
                rate = target.get("funding_rate")
                if rate is not None:
                    return [{"fundingRate": float(rate)}]
        return None

    def get_top_trader_ls_ratio(self, symbol: str) -> Optional[List]:
        data = self._cg_request(
            "top-long-short-account-ratio/history",
            {"symbol": symbol, "exchange": "Binance", "interval": "h1"}
        )
        if data and data.get("success"):
            ratios = data.get("data", [])
            if isinstance(ratios, list):
                result = []
                for r in ratios:
                    val = r.get("top_account_long_short_ratio")
                    if val is not None:
                        result.append({"ratio": float(val)})
                return result if result else None
        return None


# =============================================
# CoinGecko API 封裝
# =============================================
class CoinGeckoClient:
    BASE = config.COINGECKO_BASE_URL

    def _headers(self) -> Dict:
        h = {
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        if config.COINGECKO_API_KEY:
            h["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
            logger.debug(f"CoinGecko API Key 已載入（前 8 碼：{config.COINGECKO_API_KEY[:8]}...）")
        else:
            logger.error("COINGECKO_API_KEY 未載入！")
        return h

    def get_coins_markets(self, vs_currency: str = "usd", per_page: int = 250, page: int = 1) -> Optional[List]:
        url = f"{self.BASE}/coins/markets"
        params = {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        return _safe_request("GET", url, params=params, headers=self._headers(), rate_limiter=_coingecko_limiter)

    def get_coin_market_chart(self, coin_id: str, vs_currency: str = "usd", days: int = 14) -> Optional[Dict]:
        url = f"{self.BASE}/coins/{coin_id}/market_chart"
        params = {"vs_currency": vs_currency, "days": days, "interval": "daily"}
        return _safe_request("GET", url, params=params, headers=self._headers(), rate_limiter=_coingecko_limiter)


# 全域 Client 實例
binance = BinanceClient()
coingecko = CoinGeckoClient()