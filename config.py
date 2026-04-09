"""
BitsyBot configuration.

Override any value via environment variables or .env file.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)


# ── Mode ──────────────────────────────────────────────────────────────────────
# "paper"    → simulate live trading with real prices, no real orders
# "live"     → place real orders on Bitso (use real funds!)
# "backtest" → run strategy against stored historical candles
MODE: str = os.getenv("BITSYBOT_MODE", "paper")

# ── Exchange ──────────────────────────────────────────────────────────────────
BITSO_API_KEY: str = os.getenv("BITSO_API_KEY", "")
BITSO_API_SECRET: str = os.getenv("BITSO_API_SECRET", "")

# ── Grid parameters ───────────────────────────────────────────────────────────
BOOK: str = os.getenv("BITSYBOT_BOOK", "btc_mxn")
GRID_LOWER: float = float(os.getenv("BITSYBOT_GRID_LOWER", "800000"))    # MXN
GRID_UPPER: float = float(os.getenv("BITSYBOT_GRID_UPPER", "1200000"))   # MXN
GRID_LEVELS: int = int(os.getenv("BITSYBOT_GRID_LEVELS", "10"))
INVESTMENT_MXN: float = float(os.getenv("BITSYBOT_INVESTMENT_MXN", "5000"))

# ── Paper trading ─────────────────────────────────────────────────────────────
PAPER_INITIAL_BALANCE: float = float(os.getenv("BITSYBOT_PAPER_BALANCE", "10000"))
POLL_INTERVAL_SECONDS: int = int(os.getenv("BITSYBOT_POLL_INTERVAL", "10"))

# ── Backtest ──────────────────────────────────────────────────────────────────
BACKTEST_DAYS: int = int(os.getenv("BITSYBOT_BACKTEST_DAYS", "1825"))
BACKTEST_TIME_BUCKET: str = os.getenv("BITSYBOT_TIME_BUCKET", "1d")

# ── Strategy selector ────────────────────────────────────────────────────────
# "grid"   → original grid trading strategy (single book)
# "regime" → multi-coin regime detection strategy
STRATEGY: str = os.getenv("BITSYBOT_STRATEGY", "grid")

# ── Regime strategy ──────────────────────────────────────────────────────────
REGIME_BOOKS: list[str] = os.getenv(
    "BITSYBOT_REGIME_BOOKS",
    "btc_mxn,eth_mxn,xrp_mxn",
).split(",")
REGIME_INVESTMENT_MXN: float = float(os.getenv("BITSYBOT_REGIME_INVESTMENT_MXN", "10000"))
REGIME_POLL_INTERVAL: int = int(os.getenv("BITSYBOT_REGIME_POLL_INTERVAL", "3600"))
REGIME_ATR_THRESHOLD: float = float(os.getenv("BITSYBOT_REGIME_ATR_THRESHOLD", "2.0"))  # Lowered from 3.0
REGIME_DCA_INTERVAL_DAYS: int = int(os.getenv("BITSYBOT_REGIME_DCA_INTERVAL_DAYS", "90"))
REGIME_SELL_PCT: float = float(os.getenv("BITSYBOT_REGIME_SELL_PCT", "0.20"))           # 20% profit-taking
REGIME_BUY_PCT_BEAR: float = float(os.getenv("BITSYBOT_REGIME_BUY_PCT_BEAR", "0.05"))   # 5% buys in bear
REGIME_BUY_PCT_ACCUM: float = float(os.getenv("BITSYBOT_REGIME_BUY_PCT_ACCUM", "0.05")) # 5% buys in accumulation
REGIME_DOWNTREND_PROTECTION_PCT: float = float(os.getenv("BITSYBOT_REGIME_DOWNTREND_PROTECTION_PCT", "20.0"))
REGIME_COOLDOWN_DAYS: int = int(os.getenv("REGIME_COOLDOWN_DAYS", "30"))

# ── Notifications ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
# Optional: direct GIF URLs shown in /status reply (leave blank to disable)
TELEGRAM_GIF_PROFIT: str = os.getenv("TELEGRAM_GIF_PROFIT", "")
TELEGRAM_GIF_LOSS: str = os.getenv("TELEGRAM_GIF_LOSS", "")

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_PORT: int = int(os.getenv("BITSYBOT_DASHBOARD_PORT", "8501"))
DASHBOARD_REFRESH_SECONDS: int = int(os.getenv("BITSYBOT_DASHBOARD_REFRESH", "30"))
