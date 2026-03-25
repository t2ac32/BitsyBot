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
BACKTEST_DAYS: int = int(os.getenv("BITSYBOT_BACKTEST_DAYS", "365"))
BACKTEST_TIME_BUCKET: str = os.getenv("BITSYBOT_TIME_BUCKET", "1d")

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_PORT: int = int(os.getenv("BITSYBOT_DASHBOARD_PORT", "8501"))
DASHBOARD_REFRESH_SECONDS: int = int(os.getenv("BITSYBOT_DASHBOARD_REFRESH", "30"))
