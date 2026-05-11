"""
main.py - 監控引擎自動化版本
"""
import sys
import logging
import time
from datetime import datetime, timedelta

# 設定 logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

# 匯入模組
from src.config import config
from src.detector import check_btc_market_health, phase1_basic_filter, phase2_smart_money_filter
from src.notifier import notify_signals, notify_market_blocked, send_test_message


def validate_config() -> bool:
    """啟動前檢查必要設定是否完整"""
    errors = []
    if not config.BINANCE_API_KEY:
        errors.append("BINANCE_API_KEY 未設定")
    if not config.TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN 未設定")
    if not config.TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID 未設定")

    if errors:
        for e in errors:
            logger.error(f"設定錯誤: {e}")
        return False
    return True


def run_scan(dry_run: bool = False) -> None:
    """執行一次完整的掃描流程"""
    start_time = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'='*50}")
    logger.info(f"🔍 開始掃描 | {now}")
    logger.info(f"{'='*50}")

    if not validate_config():
        logger.error("設定不完整，終止掃描。")
        return

    # Step 1: 大盤安全過濾
    is_safe, reason = check_btc_market_health()
    if not is_safe:
        logger.warning(f"⛔ 大盤不穩，本次掃描終止：{reason}")
        if not dry_run:
            notify_market_blocked(reason)
        return

    # Step 2: 階段一 - 基礎量化過濾
    candidates = phase1_basic_filter()
    if not candidates:
        logger.info("🔎 階段一無候選幣種，本次掃描結束。")
        return

    # Step 3: 階段二 - 籌碼博弈分析
    signals = phase2_smart_money_filter(candidates)

    # Step 4: 發送通知
    elapsed = time.time() - start_time
    logger.info(f"{'='*50}")
    logger.info(f"📊 掃描完成 | 耗時: {elapsed:.1f}s | 發現訊號: {len(signals)} 個")
    logger.info(f"{'='*50}")

    if dry_run:
        logger.info("[DRY RUN] 模擬模式，不發送 Telegram 通知")
        for s in signals:
            logger.info(f"  訊號: {s.symbol} | 評分: {s.score} | 漲幅: {s.price_change_24h:.1f}%")
    else:
        if signals:
            notify_signals(signals)
        else:
            logger.info("本次無符合強力訊號，不發送通知。")


def main_loop(dry_run: bool = False):
    """無限循環監控模式"""
    mode_str = "模擬模式" if dry_run else "正式模式"
    logger.info(f"🚀 小幣監控引擎啟動 | 模式: {mode_str} | 間隔: {config.SCAN_INTERVAL_SEC // 60} 分鐘")
    
    while True:
        try:
            # 執行掃描
            run_scan(dry_run=dry_run)
            
            # 計算下次預計掃描時間
            next_run = datetime.now() + timedelta(seconds=config.SCAN_INTERVAL_SEC)
            logger.info(f"😴 掃描任務進入休眠，下次掃描預計時間: {next_run.strftime('%H:%M:%S')}")
            
            # 進入休眠
            time.sleep(config.SCAN_INTERVAL_SEC)
            
        except KeyboardInterrupt:
            logger.info("🛑 檢測到手動停止 (Ctrl+C)，正在安全退出...")
            break
        except Exception as e:
            logger.error(f"💥 監控迴圈發生非預期崩潰: {e}")
            logger.info("系統將在 60 秒後嘗試重啟...")
            time.sleep(60)


if __name__ == "__main__":
    args = sys.argv[1:]
    time.sleep(1)  # 緩衝信號

    if "--test" in args:
        logger.info("🧪 執行 Telegram 連線測試...")
        send_test_message()
        
    elif "--once" in args:
        # 💡 雲端模式：只跑一次 run_scan 就結束，不進入無限迴圈
        dry = "--dry" in args
        logger.info(f"☁️ 雲端單次觸發模式執行中 (dry_run={dry})...")
        run_scan(dry_run=dry)
        
    elif "--dry" in args:
        # 本機掛機模擬模式
        main_loop(dry_run=True)
        
    else:
        # 本機掛機正式模式
        main_loop(dry_run=False)