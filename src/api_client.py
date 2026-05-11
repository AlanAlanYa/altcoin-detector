"""
api_client.py - 所有對外 API 請求的統一處理層
包含 Rate Limiting、重試機制、錯誤處理與數值型別強健化
交易所：現貨 MEXC (Binance 相容格式) / 合約 MEXC (原生格式) / 籌碼 CoinGlass
"""
import os
import time
import logging
import requests
from typing import Optional, Dict, Any, List
from src.config import config

logger = logging.getLogger(__name__)

# 安全讀取 Config，防止缺少變數時 import 直接炸爛 (🔴 修復)
MEXC_FUTURES_BASE_URL = getattr(config, "MEXC_FUTURES_URL", "https://contract.mexc.com")


def _safe_float(val: Any, default: float = 0.0) -> float:
    """內部工具：安全地將 API 回傳的各類數值/字串轉為 float，防止 detector 運算崩潰"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


class RateLimiter:
    """簡單的滑動視窗 Rate Limiter，防止 API 請求過於頻繁"""

    def __init__(self, min_interval: float = getattr(config, "REQUEST_DELAY_SEC", 0.2)):
        self.min_interval = min_interval
        self._last_call_time: float = 0.0

    def wait(self):
        elapsed = time.time() - self._last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_time = time.time()


_rate_limiter = RateLimiter()


def _safe_request(
    method: str,
    url: str,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    retries: int = getattr(config, "MAX_RETRIES", 3),
) -> Optional[Any]:
    """
    帶重試機制的 HTTP 請求封裝。
    回傳解析後的 JSON，失敗則回傳 None。
    """
    retry_delay = getattr(config, "RETRY_DELAY_SEC", 1.0)

    for attempt in range(1, retries + 1):
        is_last = attempt == retries
        try:
            _rate_limiter.wait()
            response = requests.request(
                method, url, params=params, headers=headers, timeout=15
            )
            # 非 2xx 時記錄 response body 方便 debug
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
                wait = retry_delay * 10
                logger.warning(f"[嘗試 {attempt}/{retries}] 429 Rate Limited，等待 {wait}s")
                time.sleep(wait)
            elif not is_last:
                time.sleep(retry_delay * attempt)
        except requests.exceptions.Timeout:
            logger.warning(f"[嘗試 {attempt}/{retries}] 請求逾時: {url}")
            if not is_last:
                time.sleep(retry_delay)
        except requests.exceptions.RequestException as e:
            logger.error(f"[嘗試 {attempt}/{retries}] 請求失敗: {url} | {e}")
            if not is_last:
                time.sleep(retry_delay)
                
    logger.error(f"已達最大重試次數，放棄請求: {url}")
    return None


def _to_futures_symbol(symbol: str) -> str:
    """
    將現貨 symbol 轉換為 MEXC 合約格式。
    例：BTCUSDT → BTC_USDT
    """
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol


# =============================================
# BinanceClient（實際打 MEXC 現貨，CoinGlass 籌碼）
# =============================================
class BinanceClient:
    MEXC_SPOT_BASE = "https://api.mexc.com"
    CG_BASE = "https://open-api-v4.coinglass.com/api/futures"

    def __init__(self):
        # 支援從 env 或 config 讀取，避免單一依賴失效
        self.cg_key = os.getenv("COINGLASS_API_KEY") or getattr(config, "COINGLASS_API_KEY", None)

    # --- 第一階段：保留 MEXC 現貨（偵測 MEXC 上的爆量） ---

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> Optional[List]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/klines"
        return _safe_request("GET", url, params={"symbol": symbol, "interval": interval, "limit": limit})

    def get_ticker_24h(self, symbol: str) -> Optional[Dict]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/ticker/24hr"
        return _safe_request("GET", url, params={"symbol": symbol})

    def get_exchange_info(self) -> Optional[Dict]:
        url = f"{self.MEXC_SPOT_BASE}/api/v3/exchangeInfo"
        return _safe_request("GET", url)

    # --- 第二階段：改用 CoinGlass 全面接管籌碼數據 ---

    def _cg_request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """CoinGlass 私有請求封裝"""
        if not self.cg_key:
            logger.error("缺少 COINGLASS_API_KEY，無法請求籌碼數據")
            return None
        headers = {"accept": "application/json", "cg-api-key": self.cg_key}
        return _safe_request("GET", f"{self.CG_BASE}/{endpoint}", params=params, headers=headers)

    def get_open_interest(self, symbol: str) -> Optional[List]:
        """取得合約持倉量趨勢 (強制轉為數值，防止 detector 型別錯誤)"""
        clean_symbol = symbol.replace("USDT", "")
        data = self._cg_request("openInterest/ohlc", {"symbol": clean_symbol, "interval": "h1", "limit": 5})
        
        if data and data.get("success"):
            items = data.get("data", [])
            if isinstance(items, list):
                return [{"openInterest": _safe_float(item.get("close"))} for item in items]
        return None

    def get_cvd(self, symbol: str) -> Optional[List]:
        """直接取得 CoinGlass CVD 趨勢"""
        clean_symbol = symbol.replace("USDT", "")
        data = self._cg_request("cvd/symbol", {"symbol": clean_symbol, "interval": "h1", "limit": 5})
        
        if data and data.get("success"):
            # 確保回傳乾淨的原始列表
            return data.get("data")
        return None

    def get_funding_rate(self, symbol: str) -> Optional[List]:
        """
        🔴 修復解析錯誤：取得資金費率。
        優先尋找 Binance 數據，並確保 uMarginRate 一定是 float。
        """
        clean_symbol = symbol.replace("USDT", "")
        data = self._cg_request("fundingRate", {"symbol": clean_symbol})
        
        if data and data.get("success"):
            items = data.get("data", [])
            if isinstance(items, list) and items:
                # 優先抓取 Binance 的費率作為標準參考
                target_item = next((item for item in items if item.get("exchange") == "Binance"), items[0])
                rate = target_item.get("uMarginRate")
                
                if rate is not None:
                    return [{"fundingRate": _safe_float(rate)}]
        return None

    def get_top_trader_ls_ratio(self, symbol: str) -> Optional[List]:
        """
        🔴 修復解析錯誤：取得大戶多空比。
        過濾異常數值，強制 float 型別轉換以利後續 detector 加分邏輯運算。
        """
        clean_symbol = symbol.replace("USDT", "")
        data = self._cg_request(
            "top-long-short-account-ratio", 
            {"symbol": clean_symbol, "exchange": "Binance", "interval": "h1"}
        )
        
        if data and data.get("success"):
            ratios = data.get("data", [])
            if isinstance(ratios, list):
                result = []
                for r in ratios:
                    val = r.get("longShortRatio")
                    if val is not None:
                        result.append({"ratio": _safe_float(val)})
                return result if result else None
        return None


# =============================================
# CoinGecko API 封裝
# =============================================
class CoinGeckoClient:
    BASE = getattr(config, "COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")

    def _headers(self) -> Dict:
        h = {
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        cg_key = getattr(config, "COINGECKO_API_KEY", None)
        if cg_key:
            h["x-cg-demo-api-key"] = cg_key
            logger.debug(f"CoinGecko API Key 已載入（前 8 碼：{cg_key[:8]}...）")
        else:
            logger.error("COINGECKO_API_KEY 未載入！請確認 .env 格式：COINGECKO_API_KEY=CG-xxxx")
        return h

    def get_coins_markets(self, vs_currency: str = "usd", per_page: int = 250, page: int = 1) -> Optional[List]:
        """取得市場排行資料（含市值、24h 成交量、價格變動）"""
        url = f"{self.BASE}/coins/markets"
        params = {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        return _safe_request("GET", url, params=params, headers=self._headers())

    def get_coin_market_chart(self, coin_id: str, vs_currency: str = "usd", days: int = 14) -> Optional[Dict]:
        """取得指定幣種的歷史市場資料（用於計算 RVOL）"""
        url = f"{self.BASE}/coins/{coin_id}/market_chart"
        params = {"vs_currency": vs_currency, "days": days, "interval": "daily"}
        return _safe_request("GET", url, params=params, headers=self._headers())


# 全域 Client 實例
binance = BinanceClient()
coingecko = CoinGeckoClient()