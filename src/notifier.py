"""
notifier.py - Telegram Bot 通知模組
負責將篩選結果格式化並發送到 Telegram
"""
import logging
import requests
from typing import List
from src.config import config
from src.detector import CoinSignal

logger = logging.getLogger(__name__)


def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("Telegram Token 或 Chat ID 未設定！")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("✅ Telegram 通知發送成功！")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram 發送失敗: {e}")
        return False


def format_signal_message(signal: CoinSignal) -> str:
    if signal.score >= 90:
        score_emoji = "🔥🔥🔥"
    elif signal.score >= 80:
        score_emoji = "🔥🔥"
    elif signal.score >= 70:
        score_emoji = "🔥"
    else:
        score_emoji = "✅"

    mexc_link = f"https://www.mexc.com/exchange/{signal.symbol}"

    block = f"""<b>{signal.name} (<code>{signal.symbol}</code>)</b>
<b>當前價格：</b>${signal.price:.6g}
<b>24H 漲幅：</b>+{signal.price_change_24h:.1f}%

━━━━━━ 📊 量化指標 ━━━━━━
<b>市值：</b>${signal.market_cap_usd/1e6:.1f}M USD
<b>24H 成交量：</b>${signal.volume_24h_usd/1e6:.2f}M USD
<b>15M 漲幅：</b>{signal.change_15m:.1f}%
<b>RVOL：</b>{signal.rvol:.1f}x（15m vs 過去1h均量）
<b>訂單簿流動性：</b>${signal.orderbook_liquidity_usd:,.0f} USD

━━━━━━ 🐋 籌碼分析 ━━━━━━
<b>現貨 CVD 趨勢：</b>{signal.cvd_trend} ✅

━━━━━━ 🏆 綜合評分 ━━━━━━
<b>{signal.score} / 100</b> {score_emoji}
🔗 <a href="{mexc_link}">在 MEXC 查看 {signal.symbol}</a>""".strip()

    return block


def notify_signals(signals: List[CoinSignal]) -> None:
    if not signals:
        logger.info("本次掃描無符合條件的訊號，不發送通知。")
        return

    signals_sorted = sorted(signals, key=lambda s: s.score, reverse=True)

    header = f"🚀 <b>小幣起飛訊號！</b> 本次發現 <b>{len(signals_sorted)}</b> 個訊號，按評分由高到低：\n"
    blocks = [format_signal_message(s) for s in signals_sorted]
    body = "\n\n➖➖➖➖➖➖➖➖➖➖\n\n".join(blocks)
    full_message = header + "\n" + body

    MAX_LEN = 4096
    if len(full_message) <= MAX_LEN:
        send_telegram_message(full_message)
    else:
        logger.warning("合併訊息超過 4096 字元，改為逐筆發送。")
        send_telegram_message(header)
        for signal in signals_sorted:
            send_telegram_message(format_signal_message(signal))


def notify_market_blocked(reason: str) -> None:
    msg = f"🔒 <b>大盤過濾觸發</b>\n\n{reason}\n\n本次掃描已跳過，等待下一個週期。"
    logger.info(f"大盤過濾: {reason}")
    send_telegram_message(msg)


def send_test_message() -> None:
    msg = (
        "🤖 <b>小幣起飛監控器 - 測試訊息</b>\n\n"
        "✅ Telegram Bot 連接成功！\n"
        "✅ 系統設定正確！\n\n"
        "監控器已就緒，等待下一次掃描週期..."
    )
    result = send_telegram_message(msg)
    if result:
        print("✅ 測試訊息發送成功！請檢查你的 Telegram。")
    else:
        print("❌ 測試訊息發送失敗，請檢查 Token 和 Chat ID 設定。")