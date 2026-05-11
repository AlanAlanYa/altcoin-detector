"""
main.py - 監控引擎自動化版本（修正版）
負責定期調度 API 獲取數據、執行多階段過濾演算法並推播訊號
"""
import sys
import logging
import time
from datetime import datetime, timedelta

# 設定標準輸出日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# 引入自訂模組
from src.config import config
# 🔴 修復名稱不一致：匯入重構後的 phase2_smart_money_filter
from src.detector import check_btc_market_health, phase1_basic_filter, phase2_smart_money_filter
from src.notifier import notify_signals, notify_market_blocked, send_test_message
from src.api_client import BinanceClient   # ✅ 新增


# =====================================================
# CONFIG CHECK
# =====================================================
def validate_config() -> bool:
    errors = []

    if not config.TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN 未設定")
    if not config.TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID 未設定")

    if errors:
        for e in errors:
            logger.error(f"設定錯誤: {e}")
        return False
    return True


# =====================================================
# 主掃描流程
# =====================================================
def run_scan(dry_run: bool = False) -> None:
    start_time = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    logger.info("=" * 50)
    logger.info(f"🔍 開始掃描 | {now}")
    logger.info("=" * 50)

    if not validate_config():
        logger.error("設定不完整，終止掃描。")
        return

    # ✅ 建立 API Client 實例供後續分析讀取
    client = BinanceClient()

    # =====================================================
    # Step 1: BTC 大盤健康度檢查
    # =====================================================
    is_safe, reason = check_btc_market_health()
    if not is_safe:
        logger.warning(f"⛔ 大盤不穩，本次掃描終止：{reason}")
        if not dry_run:
            notify_market_blocked(reason)
        return

    # =====================================================
    # Step 2: Phase 1 (現貨黑馬初步過濾)
    # =====================================================
    candidates = phase1_basic_filter()
    if not candidates:
        logger.info("🔎 階段一無候選幣種符合爆量標準，本次掃描結束。")
        return

    # =====================================================
    # Step 3: Phase 2 (聰明錢籌碼深度計分)
    # =====================================================
    # 🔴 修復呼叫錯誤：使用正確的 phase2_smart_money_filter 函式
    signals = phase2_smart_money_filter(candidates, client)

    # =====================================================
    # Step 4: 結果匯出與通知
    # =====================================================
    elapsed = time.time() - start_time

    logger.info("=" * 50)
    logger.info(f"📊 掃描完成 | 耗時: {elapsed:.1f}s | 發現達標訊號: {len(signals)} 個")
    logger.info("=" * 50)

    if dry_run:
        logger.info("[DRY RUN] 模擬模式，不發送 Telegram 通知")
        for s in signals:
            logger.info(f"  達標訊號: {s.symbol} | 總評分: {s.score}")
    else:
        if signals:
            notify_signals(signals)
        else:
            logger.info("本次無達到門檻的高分強訊號")


# =====================================================
# 自動化循環引擎 (MAIN LOOP)
# =====================================================
def main_loop(dry_run: bool = False):
    mode_str = "模擬模式" if dry_run else "正式模式"

    logger.info(
        f"🚀 啟動監控引擎 | 模式: {mode_str} | 掃描間隔: {config.SCAN_INTERVAL_SEC // 60} 分鐘"
    )

    while True:
        try:
            run_scan(dry_run=dry_run)

            next_run = datetime.now() + timedelta(seconds=config.SCAN_INTERVAL_SEC)
            logger.info(f"😴 進入休眠，下次預計掃描時間: {next_run.strftime('%H:%M:%S')}")

            time.sleep(config.SCAN_INTERVAL_SEC)

        except KeyboardInterrupt:
            logger.info("🛑 收到終止指令，系統安全退出。")
            break
        except Exception as e:
            # 🛡️ 強制攔截所有未預期錯誤，防止引擎在半夜死掉
            logger.error(f"💥 引擎主迴圈遭遇崩潰例外: {e}", exc_info=True)
            logger.info("系統將在 60 秒後嘗試重啟下一次掃描...")
            time.sleep(60)


# =====================================================
# 程式進入點 (ENTRY)
# =====================================================
if __name__ == "__main__":
    args = sys.argv[1:]
    time.sleep(1)

    if "--test" in args:
        logger.info("🧪 執行 Telegram 測試推播")
        send_test_message()

    elif "--once" in args:
        dry = "--dry" in args
        logger.info(f"☁️ 執行單次掃描測試 (dry_run={dry})")
        run_scan(dry_run=dry)

    elif "--dry" in args:
        main_loop(dry_run=True)

    else:
        main_loop(dry_run=False)