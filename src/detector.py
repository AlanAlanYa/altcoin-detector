"""
detector.py - 核心篩選演算法（CoinGlass 版本）
包含黑馬現貨爆量篩選與合約聰明錢 (CVD/OI/Funding/LSR) 模組化計分
"""

import time
import logging
import numpy as np
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field
from src.config import config
from src.api_client import binance, coingecko

logger = logging.getLogger(__name__)


# =====================================================
# DATA MODEL
# =====================================================
@dataclass
class CoinSignal:
    symbol: str
    name: str
    price: float
    price_change_24h: float
    market_cap_usd: float
    volume_24h_usd: float
    rvol: float
    orderbook_liquidity_usd: float
    cvd_trend: str
    oi_trend: str
    funding_rate: float
    top_trader_ls_ratio: Optional[float]
    score: int = 0
    notes: List[str] = field(default_factory=list)


# =====================================================
# BTC 市場過濾（保留）
# =====================================================
def check_btc_market_health() -> Tuple[bool, str]:
    try:
        klines = binance.get_klines(config.BTC_SYMBOL, "4h", limit=config.BTC_EMA_PERIOD + 10)
        if not klines:
            return False, "無法取得 BTC K 線"

        closes = np.array([float(k[4]) for k in klines])
        current_price = closes[-1]
        ema_50 = _calculate_ema(closes, config.BTC_EMA_PERIOD)

        if current_price < ema_50:
            return False, f"BTC 價格 ({current_price:.2f}) 低於 EMA50 ({ema_50:.2f})"

        ticker = binance.get_ticker_24h(config.BTC_SYMBOL)
        if not ticker:
            return False, "無法取得 BTC ticker"

        change = float(ticker.get("priceChangePercent", 0))
        if change < config.MAX_BTC_DAILY_DROP:
            return False, f"BTC 單日跌幅過大 ({change:.2f}%)"

        return True, "OK"

    except Exception as e:
        logger.error(f"檢查 BTC 市場健康度時發生錯誤: {e}")
        return False, str(e)


# =====================================================
# PHASE 1（黑馬現貨初步篩選）
# =====================================================
def phase1_basic_filter() -> List[Dict[str, Any]]:
    all_coins = []
    # 抓取市值排行落在中小型區間的候選名單
    for page in range(4, 11):
        coins = coingecko.get_coins_markets(per_page=250, page=page)
        if coins:
            all_coins.extend(coins)
        time.sleep(config.REQUEST_DELAY_SEC)

    exchange_info = binance.get_exchange_info()
    if not exchange_info:
        logger.warning("無法取得交易所資訊，跳過 Phase 1 篩選")
        return []

    symbols = {s['symbol'] for s in exchange_info['symbols'] if s['status'] == 'TRADING'}
    candidates = []

    for coin in all_coins:
        symbol = coin['symbol'].upper() + "USDT"

        if symbol not in symbols:
            continue

        mcap = coin.get('market_cap', 0)
        change = coin.get('price_change_percentage_24h', 0) or 0

        if not (config.MIN_MARKET_CAP <= mcap <= config.MAX_MARKET_CAP):
            continue

        if not (config.MIN_PRICE_CHANGE_24H <= change <= config.MAX_PRICE_CHANGE_24H):
            continue

        if not _volume_spike(symbol):
            continue

        # 補齊後續階段需要的標準欄位
        coin["binance_symbol"] = symbol
        coin["price"] = coin.get("current_price", 0)
        coin["price_change_24h"] = change
        coin["market_cap_usd"] = mcap
        coin["volume_24h_usd"] = coin.get("total_volume", 0)

        coin["rvol"] = 0  # 預留欄位
        coin["orderbook_liquidity_usd"] = 0  # 預留欄位

        candidates.append(coin)

    return candidates


# =====================================================
# PHASE 2（合約聰明錢深度分析與計分）
# =====================================================
def phase2_smart_money_filter(candidates: List[Dict[str, Any]], client) -> List[CoinSignal]:
    signals = []

    for coin in candidates:
        symbol = coin.get("binance_symbol")
        if not symbol:
            continue

        score = 0
        notes = []

        # 1. CVD 趨勢檢測
        cvd_trend = _cg_cvd_trend(client, symbol)
        if cvd_trend == "上升":
            score += 30
            notes.append("CVD 上升")
        else:
            # 沒通過核心動能檢測直接剃除
            continue

        # 2. Open Interest 趨勢檢測
        oi_trend = _cg_oi_trend(client, symbol)
        if oi_trend == "減少":
            continue
        elif oi_trend == "增加":
            score += 30
            notes.append("OI 增加")

        # 3. 資金費率 (Funding Rate) 提取與過濾
        funding_data = client.get_funding_rate(symbol)
        funding_rate = 0.0
        
        if funding_data and len(funding_data) > 0:
            funding_rate = funding_data[0].get("fundingRate", 0.0)
            # 費率過高代表多頭過度擁擠，容易被狙擊
            if funding_rate >= config.MAX_FUNDING_RATE:
                continue
            score += 20
            notes.append("費率健康")
        else:
            continue

        # 4. 大戶多空比 (L/S Ratio) 提取與加分
        ls_data = client.get_top_trader_ls_ratio(symbol)
        ls_ratio = None
        
        if ls_data and len(ls_data) > 0:
            ls_ratio = ls_data[0].get("ratio")
            if ls_ratio is not None and ls_ratio > config.MIN_TOP_TRADER_LS_RATIO:
                score += 20
                notes.append("大戶偏多")

        # 總分達標則生成正式訊號
        if score >= 60:
            signals.append(
                CoinSignal(
                    symbol=symbol,
                    name=coin.get("name", ""),
                    price=coin.get("price", 0.0),
                    price_change_24h=coin.get("price_change_24h", 0.0),
                    market_cap_usd=coin.get("market_cap_usd", 0.0),
                    volume_24h_usd=coin.get("volume_24h_usd", 0.0),
                    rvol=coin.get("rvol", 0.0),
                    orderbook_liquidity_usd=coin.get("orderbook_liquidity_usd", 0.0),
                    cvd_trend=cvd_trend,
                    oi_trend=oi_trend,
                    funding_rate=funding_rate,
                    top_trader_ls_ratio=ls_ratio,
                    score=score,
                    notes=notes
                )
            )

    # 依分數高低排序輸出
    signals.sort(key=lambda x: x.score, reverse=True)
    return signals


# =====================================================
# COINGLASS HELPERS
# =====================================================
def _cg_cvd_trend(client, symbol: str) -> str:
    data = client.get_cvd(symbol)
    if not data or len(data) < 2:
        return "橫盤"

    try:
        prev_cvd = float(data[-2].get("cvd", 0))
        curr_cvd = float(data[-1].get("cvd", 0))
        return "上升" if curr_cvd > prev_cvd else "下降"
    except (ValueError, TypeError, KeyError):
        return "橫盤"


def _cg_oi_trend(client, symbol: str) -> str:
    data = client.get_open_interest(symbol)
    if not data or len(data) < 2:
        return "穩定"

    try:
        # 🔴 修復 KeyError：api_client.py 轉換後的鍵值名稱為 openInterest
        prev_oi = float(data[-2].get("openInterest", 0))
        curr_oi = float(data[-1].get("openInterest", 0))
        return "增加" if curr_oi >= prev_oi else "減少"
    except (ValueError, TypeError, KeyError):
        return "穩定"


# =====================================================
# TOOLS
# =====================================================
def _volume_spike(symbol: str) -> bool:
    try:
        klines = binance.get_klines(symbol, "15m", limit=5)
        if not klines or len(klines) < 5:
            return False

        vols = [float(k[5]) for k in klines]
        avg_vol = sum(vols[:-1]) / 4.0
        
        if avg_vol == 0:
            return False
            
        ratio = vols[-1] / avg_vol
        return ratio >= config.VOL_SPIKE_THRESHOLD

    except Exception:
        return False


def _calculate_ema(prices: np.ndarray, period: int) -> float:
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1.0 - k)
    return float(ema)