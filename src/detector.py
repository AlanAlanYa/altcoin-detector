"""
detector.py - 核心篩選演算法
階段一：基礎量化過濾
階段二：大戶籌碼博弈分析
"""
import time
import logging
import numpy as np
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from src.config import config
from src.api_client import binance, coingecko

logger = logging.getLogger(__name__)


@dataclass
class CoinSignal:
    """通過所有篩選的幣種資訊，用於組合 Telegram 通知"""
    symbol: str                    # e.g. "SOLUSDT"
    name: str                      # e.g. "Solana"
    price: float                   # 當前價格
    price_change_24h: float        # 24h 漲跌幅 %
    market_cap_usd: float          # 市值 USD
    volume_24h_usd: float          # 24h 成交量 USD
    rvol: float                    # RVOL 倍數
    orderbook_liquidity_usd: float # 訂單簿流動性 USD
    cvd_trend: str                 # "上升" / "下降" / "橫盤"
    oi_trend: str                  # "增加" / "減少" / "穩定"
    funding_rate: float            # 資金費率 %
    top_trader_ls_ratio: float     # 大戶多空比
    netflow_warning: bool = False  # 老鼠倉警告
    score: int = 0                 # 綜合評分（加分項）
    notes: List[str] = field(default_factory=list)  # 額外備註


# =============================================
# 大盤安全過濾
# =============================================
def check_btc_market_health() -> Tuple[bool, str]:
    """
    檢查 BTC 大盤是否安全。
    回傳 (is_safe: bool, reason: str)
    """
    try:
        # 取得 BTC 4H K 線
        klines = binance.get_klines(config.BTC_SYMBOL, "4h", limit=config.BTC_EMA_PERIOD + 10)
        if not klines:
            return False, "無法取得 BTC K 線資料"

        closes = np.array([float(k[4]) for k in klines])
        current_price = closes[-1]

        # 計算 50 EMA
        ema_50 = _calculate_ema(closes, config.BTC_EMA_PERIOD)
        if current_price < ema_50:
            reason = f"BTC 現價 ${current_price:,.0f} 低於 50 EMA (${ema_50:,.0f})，大盤不穩"
            logger.info(f"⛔ 大盤過濾觸發: {reason}")
            return False, reason

        # 取得 BTC 24H 漲跌幅
        ticker = binance.get_ticker_24h(config.BTC_SYMBOL)
        if not ticker:
            return False, "無法取得 BTC 24H 行情"

        btc_change_24h = float(ticker.get("priceChangePercent", 0))
        if btc_change_24h < config.MAX_BTC_DAILY_DROP:
            reason = f"BTC 單日跌幅 {btc_change_24h:.2f}% 超過警戒線 ({config.MAX_BTC_DAILY_DROP}%)"
            logger.info(f"⛔ 大盤過濾觸發: {reason}")
            return False, reason

        logger.info(f"✅ 大盤安全: BTC ${current_price:,.0f}，24H {btc_change_24h:+.2f}%，高於 50 EMA")
        return True, "BTC 大盤健康"

    except Exception as e:
        logger.error(f"大盤健康檢查發生例外: {e}")
        return False, f"大盤檢查失敗: {e}"


# =============================================
# 階段一：基礎量化過濾
# =============================================
def phase1_basic_filter() -> List[Dict]:
    """
    階段一：基礎量化過濾 + 15分鐘爆量偵測 (鎖定 Rank 251-1000 黑馬區間)
    """
    logger.info("📊 開始階段一：鎖定黑馬篩選 (Rank 251-1000 + 15m 爆量模式)...")
    
    # 1. 抓取 CoinGecko 市場資料 (跳過第一頁，抓取第 2, 3, 4 頁)
    all_coins = []
    for page in [2, 3, 4]:
        logger.info(f"正在從 CoinGecko 抓取第 {page} 頁資料...")
        coins = coingecko.get_coins_markets(per_page=250, page=page)
        
        if coins and isinstance(coins, list):
            all_coins.extend(coins)
            logger.info(f"✅ 第 {page} 頁取得 {len(coins)} 個幣種")
        else:
            logger.warning(f"❌ 第 {page} 頁抓取失敗，跳過此頁")
        
        # 💡 重要：每頁之間停頓，防止觸發 30 次/分鐘的 API 限制
        time.sleep(config.REQUEST_DELAY_SEC)

    if not all_coins:
        logger.error("無法取得任何 CoinGecko 資料，終止掃描。")
        return []

    # 2. 取得幣安目前所有 USDT 交易對 (用來比對有沒有上架)
    exchange_info = binance.get_exchange_info()
    if not exchange_info:
        logger.error("無法取得幣安交易對資訊")
        return []
    binance_symbols = {s['symbol'] for s in exchange_info['symbols'] if s['status'] == 'TRADING'}

    # 3. 開始循環過濾
    candidates = []
    for coin in all_coins:
        symbol_raw = coin['symbol'].upper()
        symbol = f"{symbol_raw}USDT"
        mcap = coin.get('market_cap', 0)
        change_24h = coin.get('price_change_percentage_24h', 0) or 0
        
        # --- 篩選 A: 市值區間 (例如 30M - 120M) ---
        if not (config.MIN_MARKET_CAP <= mcap <= config.MAX_MARKET_CAP):
            continue
        
        # --- 篩選 B: 漲幅上下限 (5% - 30%) ---
        if not (config.MIN_PRICE_CHANGE_24H <= change_24h <= config.MAX_PRICE_CHANGE_24H):
            continue

        # --- 篩選 C: 檢查是否在幣安有交易對 ---
        if symbol not in binance_symbols:
            continue

        # --- 篩選 D: 核心爆量偵測 ---
        # 只有前面過關了，才去敲幣安 API，省額度
        if check_volume_spike(symbol):
            logger.info(f"🔥 {symbol} 通過爆量篩選！市值=${mcap/1e6:.1f}M | 24h漲幅={change_24h:.1f}%")
            
            # 整理資料傳入階段二
            coin['binance_symbol'] = symbol
            candidates.append(coin)
            
    logger.info(f"階段一完成：發現 {len(candidates)} 個爆量候選幣種")
    return candidates

def check_volume_spike(symbol: str) -> bool:
    """
    計算 15 分鐘成交量爆發邏輯
    """
    try:
        # 抓取最近 5 根 15m K線
        klines = binance.get_klines(symbol, interval="15m", limit=5)
        if not klines or len(klines) < 5:
            return False

        # 提取成交量 (Index 5)
        volumes = [float(k[5]) for k in klines]
        
        current_vol = volumes[-1]            # 當前這 15 分鐘的量
        prev_1h_avg = sum(volumes[:-1]) / 4  # 過去 1 小時的平均量

        if prev_1h_avg == 0: return False
        
        ratio = current_vol / prev_1h_avg
        
        # 判定是否爆量 (依據 config 設定)
        if ratio >= config.VOL_SPIKE_THRESHOLD:
            return True
        return False
        
    except Exception as e:
        logger.warning(f"檢查 {symbol} 爆量時發生錯誤: {e}")
        return False
# =============================================
# 階段二：大戶籌碼博弈分析
# =============================================
def phase2_smart_money_filter(candidates: List[Dict]) -> List[CoinSignal]:
    """
    對通過階段一的幣種進行籌碼面深度分析。
    需同時滿足 CVD 上升、OI 增加、資金費率低、大戶偏多 四個條件。
    """
    logger.info("🔬 開始階段二：大戶籌碼博弈分析...")
    signals = []

    for coin in candidates:
        symbol = coin["symbol"]
        logger.info(f"分析 {symbol} 的籌碼資料...")

        try:
            # 條件 1：現貨 CVD 趨勢
            cvd_trend = _analyze_cvd_trend(symbol)
            if cvd_trend != "上升":
                logger.debug(f"跳過 {symbol}（CVD 趨勢為 {cvd_trend}）")
                continue

            # 條件 2：合約 OI 趨勢
            oi_trend = _analyze_oi_trend(symbol)
            if oi_trend == "減少":
                logger.debug(f"跳過 {symbol}（OI 趨勢為 {oi_trend}）")
                continue

            # 條件 3：資金費率
            funding_rate = _get_current_funding_rate(symbol)
            if funding_rate is None:
                logger.debug(f"跳過 {symbol}（無法取得資金費率）")
                continue
            if funding_rate >= config.MAX_FUNDING_RATE:
                logger.debug(f"跳過 {symbol}（資金費率={funding_rate*100:.4f}%，過熱）")
                continue

            # 條件 4：大戶多空比
            ls_ratio = _get_top_trader_ls_ratio(symbol)
            if ls_ratio is None:
                logger.debug(f"跳過 {symbol}（無法取得大戶多空比）")
                continue
            if ls_ratio < config.MIN_TOP_TRADER_LS_RATIO:
                logger.debug(f"跳過 {symbol}（大戶多空比={ls_ratio:.2f}，未達門檻）")
                continue

            # 選填：老鼠倉監控（不影響主邏輯，只加標記）
            netflow_warning, netflow_note = _check_exchange_netflow(symbol)

            # 計算綜合評分（加分項）
            score, notes = _calculate_score(coin, cvd_trend, oi_trend, funding_rate, ls_ratio)
            if netflow_warning:
                notes.append(f"⚠️ {netflow_note}")

            signal = CoinSignal(
                symbol=symbol,
                name=coin["name"],
                price=coin["price"],
                price_change_24h=coin["price_change_24h"],
                market_cap_usd=coin["market_cap_usd"],
                volume_24h_usd=coin["volume_24h_usd"],
                rvol=coin["rvol"],
                orderbook_liquidity_usd=coin["orderbook_liquidity_usd"],
                cvd_trend=cvd_trend,
                oi_trend=oi_trend,
                funding_rate=funding_rate,
                top_trader_ls_ratio=ls_ratio,
                netflow_warning=netflow_warning,
                score=score,
                notes=notes,
            )
            signals.append(signal)
            logger.info(f"🚀 {symbol} 通過所有篩選！評分: {score} | 大戶多空比: {ls_ratio:.2f} | 資金費率: {funding_rate*100:.4f}%")

        except Exception as e:
            logger.error(f"分析 {symbol} 時發生例外: {e}")
            continue

    logger.info(f"階段二完成：發現 {len(signals)} 個強力訊號！")
    return signals


# =============================================
# 內部輔助函數
# =============================================
def _calculate_ema(prices: np.ndarray, period: int) -> float:
    """計算指數移動平均 (EMA)"""
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def _get_binance_usdt_symbols() -> set:
    """取得 Binance 所有活躍的 USDT 現貨交易對"""
    info = binance.get_exchange_info()
    if not info:
        return set()
    return {
        s["symbol"]
        for s in info.get("symbols", [])
        if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
    }


def _calculate_rvol(coin_id: str, current_volume_24h: float) -> float:
    """
    計算 RVOL (相對成交量)：當前 24H 成交量 / 過去 14 天平均成交量
    使用 CoinGecko 歷史資料計算
    """
    try:
        chart = coingecko.get_coin_market_chart(coin_id, days=config.RVOL_LOOKBACK_DAYS)
        if not chart or "total_volumes" not in chart:
            return 0.0

        volumes = [v[1] for v in chart["total_volumes"]]
        if len(volumes) < 2:
            return 0.0

        # 排除最後一天（當天可能未完成）
        avg_volume = np.mean(volumes[:-1])
        if avg_volume <= 0:
            return 0.0

        return current_volume_24h / avg_volume

    except Exception as e:
        logger.warning(f"RVOL 計算失敗 ({coin_id}): {e}")
        return 0.0


def _check_orderbook_liquidity(symbol: str, current_price: float) -> float:
    """
    計算現貨訂單簿在當前價格上下 2% 內的總掛單金額 (USD)
    """
    try:
        depth = binance.get_order_book(symbol, limit=50)
        if not depth:
            return 0.0

        price_upper = current_price * 1.02
        price_lower = current_price * 0.98

        total_bid_value = sum(
            float(price) * float(qty)
            for price, qty in depth.get("bids", [])
            if float(price) >= price_lower
        )
        total_ask_value = sum(
            float(price) * float(qty)
            for price, qty in depth.get("asks", [])
            if float(price) <= price_upper
        )

        return total_bid_value + total_ask_value

    except Exception as e:
        logger.warning(f"訂單簿流動性計算失敗 ({symbol}): {e}")
        return 0.0


def _analyze_cvd_trend(symbol: str) -> str:
    """
    計算現貨 CVD (累積成交量差)：
    主動買入成交量 - 主動賣出成交量
    利用 Binance 最近成交紀錄，透過「是否吃單」判斷方向。
    回傳: "上升" / "下降" / "橫盤"
    """
    try:
        trades = binance.get_recent_trades(symbol, limit=1000)
        if not trades or len(trades) < 100:
            return "橫盤"

        # Binance API 的 isBuyerMaker=True 表示賣方主動，即 taker 是賣方
        # 所以主動買入 = isBuyerMaker == False
        cvd_series = []
        cumulative = 0.0
        segment_size = len(trades) // 5  # 分 5 段計算趨勢

        for i, trade in enumerate(trades):
            qty = float(trade["qty"])
            price = float(trade["price"])
            is_buyer_maker = trade["isBuyerMaker"]

            # 主動買入 +, 主動賣出 -
            if not is_buyer_maker:
                cumulative += qty * price
            else:
                cumulative -= qty * price

            if (i + 1) % segment_size == 0:
                cvd_series.append(cumulative)

        if len(cvd_series) < 3:
            return "橫盤"

        # 判斷趨勢：後半段均值是否高於前半段均值
        first_half = np.mean(cvd_series[:len(cvd_series)//2])
        second_half = np.mean(cvd_series[len(cvd_series)//2:])
        delta = (second_half - first_half) / (abs(first_half) + 1)

        if delta > 0.05:
            return "上升"
        elif delta < -0.05:
            return "下降"
        else:
            return "橫盤"

    except Exception as e:
        logger.warning(f"CVD 分析失敗 ({symbol}): {e}")
        return "橫盤"


def _analyze_oi_trend(symbol: str) -> str:
    """
    分析合約未平倉量 (OI) 趨勢。
    若 symbol 無合約（非期貨幣種），標記為 "N/A" 並跳過此條件。
    """
    try:
        oi_hist = binance.get_open_interest_hist(symbol, period="15m", limit=12)
        if not oi_hist:
            # 可能不是合約幣種，給予寬鬆判斷
            return "穩定"

        oi_values = [float(item["sumOpenInterest"]) for item in oi_hist]
        if len(oi_values) < 4:
            return "穩定"

        first_avg = np.mean(oi_values[:len(oi_values)//2])
        second_avg = np.mean(oi_values[len(oi_values)//2:])
        delta_pct = (second_avg - first_avg) / (first_avg + 1) * 100

        if delta_pct > 2:
            return "增加"
        elif delta_pct < -2:
            return "減少"
        else:
            return "穩定"

    except Exception as e:
        logger.warning(f"OI 趨勢分析失敗 ({symbol}): {e}")
        return "穩定"


def _get_current_funding_rate(symbol: str) -> Optional[float]:
    """取得當前資金費率（小數形式，如 0.0001 = 0.01%）"""
    try:
        data = binance.get_funding_rate(symbol)
        if not data:
            return None
        return float(data[0]["fundingRate"])
    except Exception as e:
        logger.warning(f"資金費率取得失敗 ({symbol}): {e}")
        return None


def _get_top_trader_ls_ratio(symbol: str) -> Optional[float]:
    """取得最新大戶持倉多空比"""
    try:
        data = binance.get_top_trader_ls_ratio(symbol, period="15m", limit=3)
        if not data:
            return None
        # 取最新的一筆
        return float(data[-1]["longShortRatio"])
    except Exception as e:
        logger.warning(f"大戶多空比取得失敗 ({symbol}): {e}")
        return None


def _check_exchange_netflow(symbol: str) -> Tuple[bool, str]:
    """
    老鼠倉監控：偵測近 2H 是否有異常大額代幣流入幣安。
    利用 Binance 成交資料中的大單異常分析（簡化版）。
    實際生產環境建議接 Glassnode / Nansen / CryptoQuant API。
    """
    try:
        trades = binance.get_recent_trades(symbol, limit=500)
        if not trades:
            return False, ""

        prices = [float(t["price"]) for t in trades]
        avg_price = np.mean(prices)

        # 計算每筆交易量（USD）
        trade_values = [float(t["price"]) * float(t["qty"]) for t in trades]
        avg_trade_value = np.mean(trade_values)
        large_threshold = avg_trade_value * 10  # 超過平均 10 倍視為大單

        large_sell_orders = [
            v for i, v in enumerate(trade_values)
            if v > large_threshold and trades[i]["isBuyerMaker"]
        ]

        if large_sell_orders:
            total_large_sell = sum(large_sell_orders)
            if total_large_sell > 100_000:  # 超過 $10萬 USD 大量主動賣出
                return True, f"偵測到異常大額賣單 ${total_large_sell:,.0f} USD（可能老鼠倉出貨）"

        return False, ""

    except Exception as e:
        logger.debug(f"老鼠倉監控失敗 ({symbol}): {e}")
        return False, ""


def _calculate_score(
    coin: Dict,
    cvd_trend: str,
    oi_trend: str,
    funding_rate: float,
    ls_ratio: float,
) -> Tuple[int, List[str]]:
    """計算綜合評分與備註（加分項，不影響主篩選邏輯）"""
    score = 60  # 通過所有條件的基礎分
    notes = []

    # RVOL 加分
    if coin["rvol"] >= 5:
        score += 10
        notes.append(f"🔥 超強 RVOL: {coin['rvol']:.1f}x（異常熱度）")
    elif coin["rvol"] >= 3:
        score += 5

    # OI 增加加分
    if oi_trend == "增加":
        score += 10
        notes.append("📈 合約 OI 持續增加（新資金流入）")
    elif oi_trend == "穩定":
        score += 3

    # 資金費率極低（反向加分）
    if funding_rate < 0:
        score += 10
        notes.append(f"💎 資金費率為負 ({funding_rate*100:.4f}%)（市場對多頭仍有懷疑，上漲空間大）")
    elif funding_rate < 0.0001:
        score += 5

    # 大戶多空比加分
    if ls_ratio >= 1.5:
        score += 10
        notes.append(f"🐋 大戶強力做多（多空比: {ls_ratio:.2f}）")
    elif ls_ratio >= 1.1:
        score += 5

    # 漲幅加分
    change = coin["price_change_24h"]
    if change >= 50:
        score += 5
        notes.append(f"🚀 24H 強勢拉升 {change:.1f}%")

    return min(score, 100), notes
def check_volume_spike(symbol: str) -> bool:
    """
    檢查 15 分鐘成交量是否爆發 (3倍於過去 1 小時均值)
    """
    try:
        # 抓取最近 5 根 15m K線 (前 4 根算均值，第 5 根是當前)
        # 數據格式 [開盤時間, 開, 高, 低, 收, 成交量, ...]
        klines = binance.get_klines(symbol, interval="15m", limit=5)
        
        if not klines or len(klines) < 5:
            return False

        # 提取成交量 (第 6 個元素)
        volumes = [float(k[5]) for k in klines]
        
        current_vol = volumes[-1]            # 當前 15 分鐘的量
        prev_1h_avg = sum(volumes[:-1]) / 4  # 過去 1 小時 (4根 15m) 的平均量

        if prev_1h_avg == 0: 
            return False
        
        ratio = current_vol / prev_1h_avg
        
        # 判定是否爆量
        if ratio >= config.VOL_SPIKE_THRESHOLD:
            logger.info(f"🔥 {symbol} 偵測到爆量！成交量比值: {ratio:.2f}x")
            return True
            
        return False
    except Exception as e:
        logger.error(f"成交量檢查出錯 ({symbol}): {e}")
        return False