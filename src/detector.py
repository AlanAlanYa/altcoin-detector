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
    change_15m: float                  # ✅ 新增：15m 漲幅
    market_cap_usd: float
    volume_24h_usd: float
    rvol: float                        # ✅ 真實 RVOL（當前15m vs 過去1h均量）
    orderbook_liquidity_usd: float
    cvd_trend: str
    oi_trend: str
    funding_rate: float
    top_trader_ls_ratio: Optional[float]
    score: int = 0
    notes: List[str] = field(default_factory=list)


# =====================================================
# BTC 市場過濾
# =====================================================
def check_btc_market_health() -> Tuple[bool, str]:
    try:
        klines = binance.get_klines(config.BTC_SYMBOL, "4h", limit=config.BTC_EMA_PERIOD + 10)
        if not klines:
            return False, "無法取得 BTC K 線"

        closes = np.array([float(k[4]) for k in klines])
        current_price = closes[-1]
        ema_20 = _calculate_ema(closes, 20)

        if current_price < ema_20:
            reason = f"BTC 價格 ({current_price:.2f}) 低於 EMA20 ({ema_20:.2f})"
            logger.warning(f"⛔ 大盤過濾：{reason}")
            return False, reason

        ticker = binance.get_ticker_24h(config.BTC_SYMBOL)
        if not ticker:
            return False, "無法取得 BTC ticker"

        change = float(ticker.get("priceChangePercent", 0))
        if change < config.MAX_BTC_DAILY_DROP:
            reason = f"BTC 單日跌幅過大 ({change:.2f}%)"
            logger.warning(f"⛔ 大盤過濾：{reason}")
            return False, reason

        logger.info(f"✅ 大盤安全：BTC ${current_price:,.0f} | 24H {change:+.2f}% | EMA20 ${ema_20:,.0f}")
        return True, "OK"

    except Exception as e:
        logger.error(f"檢查 BTC 市場健康度時發生錯誤: {e}")
        return False, str(e)


# =====================================================
# PHASE 1（黑馬現貨初步篩選）
# =====================================================
def phase1_basic_filter() -> List[Dict[str, Any]]:
    all_coins = []
    for page in range(1, 5):
        coins = coingecko.get_coins_markets(per_page=250, page=page)
        if coins:
            all_coins.extend(coins)
        time.sleep(config.REQUEST_DELAY_SEC)
    logger.info(f"CoinGecko 共抓到 {len(all_coins)} 個幣種")

    exchange_info = binance.get_exchange_info()
    if not exchange_info:
        logger.warning("無法取得交易所資訊，跳過 Phase 1 篩選")
        return []

    symbols = {s['symbol'] for s in exchange_info['symbols'] if s['status'] == '1'}
    candidates = []

    for coin in all_coins:
        symbol = coin['symbol'].upper() + "USDT"

        if symbol not in symbols:
            logger.debug(f"⛔ {symbol} MEXC 未上架")
            continue

        mcap = coin.get('market_cap', 0)
        change_24h = coin.get('price_change_percentage_24h', 0) or 0

        if not (config.MIN_MARKET_CAP <= mcap <= config.MAX_MARKET_CAP):
            logger.debug(f"⛔ {symbol} 市值不符：{mcap/1e6:.1f}M")
            continue

        # ✅ 回傳三個值：爆量判斷、15m漲幅、真實RVOL
        is_spike, change_15m, rvol = _check_spike_and_momentum(symbol)
        if not is_spike:
            logger.debug(f"⛔ {symbol} 爆量不足，跳過")
            continue
        if change_15m < config.MIN_PRICE_CHANGE_15M:
            logger.debug(f"⛔ {symbol} 15m 漲幅不足：{change_15m:.1f}%")
            continue

        logger.info(f"🔥 {symbol} 通過！市值=${mcap/1e6:.1f}M | 24H={change_24h:.1f}% | 15M={change_15m:.1f}% | RVOL={rvol:.1f}x")

        coin["binance_symbol"] = symbol
        coin["price"] = coin.get("current_price", 0)
        coin["price_change_24h"] = change_24h
        coin["change_15m"] = change_15m        # ✅ 15m 漲幅單獨存
        coin["market_cap_usd"] = mcap
        coin["volume_24h_usd"] = coin.get("total_volume", 0)
        coin["rvol"] = rvol                    # ✅ 真實 RVOL
        coin["orderbook_liquidity_usd"] = 0.0

        candidates.append(coin)

    logger.info(f"✅ Phase1 通過 {len(candidates)} 個幣種：{[c['binance_symbol'] for c in candidates]}")
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
        cvd_label, cvd_delta = _cg_cvd_trend(client, symbol)
        if cvd_label != "上升":
            logger.info(f"❌ {symbol} 淘汰：CVD={cvd_label}")
            continue
        score += 30
        cvd_display = f"上升（delta={cvd_delta:.2f}）"
        coin["orderbook_liquidity_usd"] = _orderbook_liquidity(symbol)

        # 2. OI 趨勢檢測
        oi_trend = _cg_oi_trend(client, symbol)
        if oi_trend == "減少":
            logger.info(f"❌ {symbol} 淘汰：OI 減少")
            continue
        elif oi_trend == "增加":
            score += 30
            notes.append("OI 增加")
        elif oi_trend == "穩定":
            score += 10

        # 3. 資金費率
        funding_data = client.get_funding_rate(symbol)
        funding_rate = 0.0
        if funding_data and len(funding_data) > 0:
            funding_rate = funding_data[0].get("fundingRate", 0.0)
            if funding_rate >= config.MAX_FUNDING_RATE:
                logger.info(f"❌ {symbol} 淘汰：資金費率過熱={funding_rate:.4f}")
                continue
            score += 20
            notes.append("費率健康")
        else:
            notes.append("⚠️ 無資金費率數據（預設中性）")

        # 4. 大戶多空比
        ls_data = client.get_top_trader_ls_ratio(symbol)
        ls_ratio = None
        if ls_data and len(ls_data) > 0:
            ls_ratio = ls_data[0].get("ratio")
            if ls_ratio is not None and ls_ratio > config.MIN_TOP_TRADER_LS_RATIO:
                score += 20
                notes.append("大戶偏多")

        logger.info(f"📊 {symbol} 計分：{score}分 | CVD={cvd_display} | OI={oi_trend} | 費率={funding_rate:.4f} | LS={ls_ratio}")

        if score >= 40:
            signals.append(
                CoinSignal(
                    symbol=symbol,
                    name=coin.get("name", ""),
                    price=coin.get("price", 0.0),
                    price_change_24h=coin.get("price_change_24h", 0.0),
                    change_15m=coin.get("change_15m", 0.0),         # ✅
                    market_cap_usd=coin.get("market_cap_usd", 0.0),
                    volume_24h_usd=coin.get("volume_24h_usd", 0.0),
                    rvol=coin.get("rvol", 0.0),                     # ✅ 真實 RVOL
                    orderbook_liquidity_usd=coin.get("orderbook_liquidity_usd", 0.0),
                    cvd_trend=cvd_display,
                    oi_trend=oi_trend,
                    funding_rate=funding_rate,
                    top_trader_ls_ratio=ls_ratio,
                    score=score,
                    notes=notes
                )
            )

    signals.sort(key=lambda x: x.score, reverse=True)
    return signals


# =====================================================
# COINGLASS HELPERS
# =====================================================
def _cg_cvd_trend(client, symbol: str) -> Tuple[str, float]:
    """回傳 (trend, delta)"""
    try:
        trades = binance.get_recent_trades(symbol, limit=500)
        if not trades or len(trades) < 50:
            return "橫盤", 0.0

        cumulative = 0.0
        segments = []
        size = max(len(trades) // 5, 1)

        for i, t in enumerate(trades):
            val = float(t["price"]) * float(t["qty"])
            cumulative += val if not t["isBuyerMaker"] else -val
            if (i + 1) % size == 0:
                segments.append(cumulative)

        if len(segments) < 2:
            return "橫盤", 0.0

        first = sum(segments[:len(segments)//2]) / (len(segments)//2)
        second = sum(segments[len(segments)//2:]) / (len(segments) - len(segments)//2)
        delta = (second - first) / (abs(first) + 1)

        if delta > config.CVD_DELTA_THRESHOLD:
            return "上升", delta
        elif delta < -config.CVD_DELTA_THRESHOLD:
            return "下降", delta
        else:
            return "橫盤", delta

    except Exception:
        return "橫盤", 0.0


def _cg_oi_trend(client, symbol: str) -> str:
    data = client.get_open_interest(symbol)
    if not data or len(data) < 2:
        return "穩定"
    try:
        prev_oi = float(data[-2].get("openInterest", 0))
        curr_oi = float(data[-1].get("openInterest", 0))
        return "增加" if curr_oi >= prev_oi else "減少"
    except (ValueError, TypeError, KeyError):
        return "穩定"


# =====================================================
# TOOLS
# =====================================================
def _check_spike_and_momentum(symbol: str) -> Tuple[bool, float, float]:
    """
    一次 API 請求同時計算爆量、15m 漲幅、真實 RVOL。
    回傳 (is_spike: bool, change_15m: float, rvol: float)
    """
    try:
        klines = binance.get_klines(symbol, "15m", limit=6)
        if not klines or len(klines) < 6:
            return False, 0.0, 0.0

        vols = [float(k[5]) for k in klines[:-1]]
        avg_vol = sum(vols[:-1]) / 4
        if avg_vol == 0:
            return False, 0.0, 0.0

        rvol = vols[-1] / avg_vol                              # ✅ 真實 RVOL
        is_spike = rvol >= config.VOL_SPIKE_THRESHOLD

        k = klines[-2]
        open_price = float(k[1])
        close_price = float(k[4])
        change_15m = (close_price - open_price) / open_price * 100 if open_price else 0.0

        return is_spike, change_15m, rvol

    except Exception:
        return False, 0.0, 0.0


def _orderbook_liquidity(symbol: str, depth_pct: float = 0.05) -> float:
    """計算距離現價 ±5% 範圍內的訂單簿流動性（USD）"""
    try:
        ob = binance.get_orderbook(symbol, limit=100)
        ticker = binance.get_ticker_24h(symbol)
        if not ob or not ticker:
            return 0.0

        mid_price = float(ticker.get("lastPrice", 0))
        if mid_price == 0:
            return 0.0

        low = mid_price * (1 - depth_pct)
        high = mid_price * (1 + depth_pct)

        total = 0.0
        for price, qty in ob.get("bids", []):
            if float(price) >= low:
                total += float(price) * float(qty)
        for price, qty in ob.get("asks", []):
            if float(price) <= high:
                total += float(price) * float(qty)

        return total

    except Exception:
        return 0.0


def _calculate_ema(prices: np.ndarray, period: int) -> float:
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1.0 - k)
    return float(ema)