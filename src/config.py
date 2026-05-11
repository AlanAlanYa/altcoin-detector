"""
config.py - 集中管理所有設定參數（CoinGlass 版）
包含環境變數載入、型別轉換與端點補全
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# =====================================================
# ENV PATH
# =====================================================
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"

if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    print(f"❌ 警告：找不到 .env 檔案：{env_path}，將使用系統環境變數或預設值。")


# =====================================================
# CONFIG CLASS
# =====================================================
class Config:

    # ========================
    # API KEYS
    # ========================
    COINGECKO_API_KEY: str = os.getenv("COINGECKO_API_KEY", "").strip()
    COINGLASS_API_KEY: str = os.getenv("COINGLASS_API_KEY", "").strip()   # ✅ 新增
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # ========================
    # PHASE 1（黑馬篩選）
    # ========================
    MIN_MARKET_CAP: float = float(os.getenv("MIN_MARKET_CAP", 30_000_000))
    MAX_MARKET_CAP: float = float(os.getenv("MAX_MARKET_CAP", 120_000_000))

    MIN_PRICE_CHANGE_24H: float = float(os.getenv("MIN_PRICE_CHANGE_24H", 5.0))
    MAX_PRICE_CHANGE_24H: float = float(os.getenv("MAX_PRICE_CHANGE_24H", 30.0))

    VOL_SPIKE_THRESHOLD: float = float(os.getenv("VOL_SPIKE_THRESHOLD", 3.0))

    # ========================
    # PHASE 2（籌碼）
    # ========================
    MAX_FUNDING_RATE: float = float(os.getenv("MAX_FUNDING_RATE", 0.0005))
    MIN_TOP_TRADER_LS_RATIO: float = float(os.getenv("MIN_TOP_TRADER_LS_RATIO", 1.1))

    # （保留但目前未用，可未來擴充）
    MIN_RVOL: float = float(os.getenv("MIN_RVOL", 3.0))
    MIN_ORDERBOOK_LIQUIDITY: float = float(os.getenv("MIN_ORDERBOOK_LIQUIDITY", 50_000))

    # ========================
    # BTC 市場保護
    # ========================
    MAX_BTC_DAILY_DROP: float = float(os.getenv("MAX_BTC_DAILY_DROP", -3.0))
    BTC_EMA_PERIOD: int = 50
    BTC_SYMBOL: str = "BTCUSDT"

    # ========================
    # 系統運行
    # ========================
    REQUEST_DELAY_SEC: float = float(os.getenv("REQUEST_DELAY_SEC", 2.1))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", 3))
    RETRY_DELAY_SEC: float = float(os.getenv("RETRY_DELAY_SEC", 5.0))

    # 🟡 修復死參數：支援從 .env 動態調整掃描間隔（預設 900 秒 / 15 分鐘）
    SCAN_INTERVAL_SEC: int = int(os.getenv("SCAN_INTERVAL_SEC", 900))

    # ========================
    # API ENDPOINTS
    # ========================
    MEXC_SPOT_URL: str = "https://api.mexc.com"
    # 🔴 修復缺漏：加入合約基礎 URL，防止 api_client 載入時炸爛
    MEXC_FUTURES_URL: str = os.getenv("MEXC_FUTURES_URL", "https://contract.mexc.com")
    
    COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"
    COINGLASS_BASE_URL: str = "https://open-api-v4.coinglass.com/api/futures"


# 實例化全域變數供其他模組引入
config = Config()