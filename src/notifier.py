"""
notifier.py - Telegram Bot 通知模組
負責將篩選結果格式化並發送到 Telegram
"""
import logging
import requests
from typing import List, Optional
from src.config import config
from src.detector import CoinSignal

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{{}}/sendMessage"


def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    發送 Telegram 訊息。
    使用 HTML 格式支援粗體、連結等排版。
    """
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
    """
    將 CoinSignal 格式化為 Telegram HTML 訊息。
    """
    # 評分對應 emoji
    if signal.score >= 90:
        score_emoji = "🔥🔥🔥"
    elif signal.score >= 80:
        score_emoji = "🔥🔥"
    elif signal.score >= 70:
        score_emoji = "🔥"
    else:
        score_emoji = "✅"

    # 資金費率顏色提示
    if signal.funding_rate < 0:
        fr_note = "（極佳）"
    elif signal.funding_rate < 0.0001:
        fr_note = "（低）"
    elif signal.funding_rate < 0.0003:
        fr_note = "（正常）"
    else:
        fr_note = "（偏高）"

    # 備註清單
    notes_lines = ""
    if signal.notes:
        notes_text = "\n".join(f"  • {note}" for note in signal.notes)
        notes_lines = f"\n\n<b>💡 加分亮點：</b>\n{notes_text}"

    mexc_link = f"https://www.mexc.com/exchange/{signal.symbol}"

    message = f"""
🚀 <b>小幣起飛訊號！</b> {score_emoji}

<b>幣種：</b>{signal.name} (<code>{signal.symbol}</code>)
<b>當前價格：</b>${signal.price:.6g}
<b>24H 漲幅：</b><b>+{signal.price_change_24h:.1f}%</b>

━━━━━━ 📊 量化指標 ━━━━━━
<b>市值：</b>${signal.market_cap_usd/1e6:.1f}M USD
<b>24H 成交量：</b>${signal.volume_24h_usd/1e6:.2f}M USD
<b>RVOL：</b>{signal.rvol:.1f}x（相對成交量）
<b>訂單簿流動性：</b>${signal.orderbook_liquidity_usd:,.0f} USD

━━━━━━ 🐋 籌碼分析 ━━━━━━
<b>現貨 CVD 趨勢：</b>{signal.cvd_trend} ✅
<b>合約 OI 趨勢：</b>{signal.oi_trend} ✅
<b>資金費率：</b>{signal.funding_rate*100:.4f}% {fr_note} ✅
<b>大戶多空比：</b>{f"{signal.top_trader_ls_ratio:.2f}" if signal.top_trader_ls_ratio is not None else "N/A"} ✅
━━━━━━ 🏆 綜合評分 ━━━━━━
<b>{signal.score} / 100</b> {score_emoji}
{notes_lines}

🔗 <a href="{mexc_link}">在 MEXC 查看 {signal.symbol}</a>

⚠️ <i>此訊號僅供參考，不構成投資建議。請自行做好風險管理。</i>
""".strip()

    return message


def notify_signals(signals: List[CoinSignal]) -> None:
    """
    發送所有訊號通知到 Telegram。
    若無訊號，可選擇發送「本次掃描無訊號」的靜默日誌（預設不發）。
    """
    if not signals:
        logger.info("本次掃描無符合條件的訊號，不發送通知。")
        return

    # 依評分排序，最強的先發
    signals_sorted = sorted(signals, key=lambda s: s.score, reverse=True)

    # 發送摘要標頭（多於 1 個訊號時）
    if len(signals_sorted) > 1:
        header = (
            f"📡 <b>本次掃描發現 {len(signals_sorted)} 個訊號！</b>\n"
            f"以下按評分由高到低列出："
        )
        send_telegram_message(header)

    # 逐一發送詳細訊號
    for signal in signals_sorted:
        msg = format_signal_message(signal)
        send_telegram_message(msg)


def notify_market_blocked(reason: str) -> None:
    """
    發送大盤過濾觸發通知（靜默模式，可關閉）。
    生產環境可考慮每天只發一次，避免打擾。
    """
    # 注意：若不想在大盤不好時收到通知，可直接把這裡的 send 刪掉
    msg = f"🔒 <b>大盤過濾觸發</b>\n\n{reason}\n\n本次掃描已跳過，等待下一個週期。"
    logger.info(f"大盤過濾: {reason}")
    # send_telegram_message(msg)  # 預設關閉，開啟此行可收到大盤警告通知


def send_test_message() -> None:
    """發送測試訊息，確認 Bot 設定正確"""
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
