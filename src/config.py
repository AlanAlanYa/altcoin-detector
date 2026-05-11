"""
config.py - 集中管理所有設定參數 (加強路徑識別版)
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 💡 自動定位 .env 檔案的絕對路徑
# __file__ 是 config.py 本身，.parent 是 src/，再一個 .parent 就是專案根目錄
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"

# 強制載入指定路徑的 .env，並回傳是否載入成功
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
    # print(f"✅ 已成功載入環境變數檔: {env_path}") # 除錯用
else:
    print(f"❌ 警告：在 {env_path} 找不到 .env 檔案！")

class Config:
    # === API 金鑰 ===
    # 使用 strip() 確保不會因為複製貼上多出的空格導致認證失敗
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "").strip()
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "").strip()
    COINGECKO_API_KEY: str = os.getenv("COINGECKO_API_KEY", "").strip()
    COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"
    
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # === 核心篩選參數 (黑馬爆量策略) ===
    # 市值改為 30M - 120M (Rank 251-1000 區間)
    MIN_MARKET_CAP: float = float(os.getenv("MIN_MARKET_CAP", 30_000_000))
    MAX_MARKET_CAP: float = float(os.getenv("MAX_MARKET_CAP", 120_000_000))
    
    # 漲幅過濾：5% 啟動，超過 30% 不追
    MIN_PRICE_CHANGE_24H: float = float(os.getenv("MIN_PRICE_CHANGE_24H", 5.0))
    MAX_PRICE_CHANGE_24H: float = float(os.getenv("MAX_PRICE_CHANGE_24H", 30.0))
    
    # 💥 15分鐘爆量門檻 (剛才漏掉的關鍵)
    VOL_SPIKE_THRESHOLD: float = float(os.getenv("VOL_SPIKE_THRESHOLD", 3.0))

    # === 籌碼分析參數 (維持原樣) ===
    MIN_RVOL: float = float(os.getenv("MIN_RVOL", 3.0))
    MIN_ORDERBOOK_LIQUIDITY: float = float(os.getenv("MIN_ORDERBOOK_LIQUIDITY", 50_000))
    MIN_TOP_TRADER_LS_RATIO: float = float(os.getenv("MIN_TOP_TRADER_LS_RATIO", 1.1))
    MAX_FUNDING_RATE: float = float(os.getenv("MAX_FUNDING_RATE", 0.0005))
    
    # === 大盤保護機制 ===
    MAX_BTC_DAILY_DROP: float = float(os.getenv("MAX_BTC_DAILY_DROP", -3.0))
    BTC_EMA_PERIOD: int = 50
    BTC_SYMBOL: str = "BTCUSDT"

    # === 運作設定 ===
    REQUEST_DELAY_SEC: float = 2.1  # 符合 CoinGecko 30次/分鐘限制
    MAX_RETRIES: int = 3
    RETRY_DELAY_SEC: float = 5.0
    SCAN_INTERVAL_SEC: int = 1800  # 半小時掃描一次

    BINANCE_BASE_URL: str = "https://api.binance.com"
    COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"
    ORDER_BOOK_DEPTH: int = 20
    CVD_LOOKBACK_HOURS: int = 6
    NETFLOW_CHECK_HOURS: int = 2

config = Config()